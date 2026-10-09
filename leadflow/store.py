"""SQLite persistence. One file holds leads, the idempotency ledger, routing state and the outbox.

Design notes
- Every inbound record (webhook event or CSV row) has an idempotency key. The key is written to
  ``ingest_log`` in the *same transaction* as the lead insert/merge, routing-pointer advance and
  notification insert. Either all of it commits or none of it does, which is what makes replays
  and crash-restarts safe.
- Writers use ``BEGIN IMMEDIATE`` so concurrent webhook threads serialise on the write lock
  instead of racing on "check key, then insert".
"""
import json
import os
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    email           TEXT NOT NULL UNIQUE,
    company_key     TEXT,
    company_domain  TEXT,
    owner           TEXT,
    territory       TEXT,
    tier            TEXT,
    score           INTEGER,
    data            TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_leads_company_key ON leads(company_key);
CREATE INDEX IF NOT EXISTS ix_leads_company_domain ON leads(company_domain);

-- every email ever seen for a lead (primary + merged alternates) -> exact-match dedupe
CREATE TABLE IF NOT EXISTS lead_emails (
    email   TEXT PRIMARY KEY,
    lead_id INTEGER NOT NULL REFERENCES leads(id)
);

-- idempotency ledger + audit trail: one row per processed event / CSV row
CREATE TABLE IF NOT EXISTS ingest_log (
    seq         INTEGER PRIMARY KEY AUTOINCREMENT,
    key         TEXT NOT NULL UNIQUE,
    source      TEXT NOT NULL,
    outcome     TEXT NOT NULL,          -- created | merged | rejected
    reason      TEXT,
    match_type  TEXT,                   -- email | fuzzy | NULL
    lead_id     INTEGER,
    fixes       TEXT,                   -- JSON list of normalisation fixes applied
    raw         TEXT,                   -- JSON of the inbound record (for rejects / audit)
    processed_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE TABLE IF NOT EXISTS replay_log (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    key     TEXT NOT NULL,
    source  TEXT NOT NULL,
    at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE TABLE IF NOT EXISTS http_rejections (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    path    TEXT,
    status  INTEGER,
    reason  TEXT,
    at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE TABLE IF NOT EXISTS routing_state (
    pool        TEXT PRIMARY KEY,
    next_index  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS notifications (
    lead_id     INTEGER PRIMARY KEY REFERENCES leads(id),
    payload     TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE TABLE IF NOT EXISTS batch_runs (
    run_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    file_name    TEXT NOT NULL,
    file_hash    TEXT NOT NULL,
    status       TEXT NOT NULL,         -- running | completed | crashed
    rows_total   INTEGER,
    stats        TEXT,
    started_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    finished_at  TEXT
);
"""


def connect(db_path):
    if db_path != ":memory:":
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=30, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    return conn


def key_exists(conn, key):
    return conn.execute("SELECT 1 FROM ingest_log WHERE key = ?", (key,)).fetchone() is not None


def load_lead(conn, lead_id):
    row = conn.execute("SELECT data FROM leads WHERE id = ?", (lead_id,)).fetchone()
    return json.loads(row["data"]) if row else None


def insert_lead(conn, lead):
    cur = conn.execute(
        "INSERT INTO leads (email, company_key, company_domain, owner, territory, tier, score, data) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (lead["email"], lead.get("company_key"), lead.get("company_domain"), lead.get("owner"),
         lead.get("territory"), lead.get("tier"), lead.get("score"), "{}"),
    )
    lead_id = cur.lastrowid
    lead["id"] = lead_id
    save_lead(conn, lead)
    return lead_id


def save_lead(conn, lead):
    conn.execute(
        "UPDATE leads SET email=?, company_key=?, company_domain=?, owner=?, territory=?, tier=?, score=?, data=? "
        "WHERE id=?",
        (lead["email"], lead.get("company_key"), lead.get("company_domain"), lead.get("owner"),
         lead.get("territory"), lead.get("tier"), lead.get("score"), json.dumps(lead, sort_keys=True), lead["id"]),
    )
    for email in lead.get("emails", [lead["email"]]):
        conn.execute("INSERT OR IGNORE INTO lead_emails (email, lead_id) VALUES (?, ?)", (email, lead["id"]))


def all_leads(conn):
    return [json.loads(r["data"]) for r in conn.execute("SELECT data FROM leads ORDER BY id")]


def log_http_rejection(conn, path, status, reason):
    try:
        conn.execute("INSERT INTO http_rejections (path, status, reason) VALUES (?,?,?)", (path, status, reason))
    except sqlite3.Error:
        pass  # audit logging must never turn a 4xx into a 500
