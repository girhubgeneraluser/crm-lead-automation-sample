"""Trade-show CSV import: tolerant parsing + resumable, idempotent processing.

- Encoding: UTF-8 (with or without BOM), falling back to Windows-1252 (Excel exports).
- NUL bytes are stripped (they make the csv module abort the whole file).
- Headers are matched by alias ("E-mail", "email_address", "Surname", ...), case/space-insensitive.
- Rows with the wrong number of columns are quarantined as ``corrupt_row`` - not guessed at.
- Idempotency key per row = file hash + hash of the row's cells. Identical rows inside one
  file collapse to one key (counted as duplicate rows). Each row is committed in its own
  transaction together with its ledger entry, so a crash loses at most the in-flight row and
  re-running the same command resumes where it stopped.
"""
import csv
import hashlib
import io
import json
import os
import re
from datetime import datetime

from . import pipeline, store

HEADER_ALIASES = {
    "first_name": ["first name", "firstname", "first", "given name", "fname", "forename"],
    "last_name": ["last name", "lastname", "surname", "family name", "lname"],
    "full_name": ["name", "full name", "contact name", "contact"],
    "email": ["email", "e mail", "email address", "e mail address", "mail", "work email"],
    "phone": ["phone", "phone number", "telephone", "tel", "mobile", "cell", "phone no"],
    "company": ["company", "company name", "organisation", "organization", "org", "employer", "account"],
    "job_title": ["job title", "title", "position", "role", "job"],
    "country": ["country", "country/region", "country region", "nation"],
    "company_size": ["company size", "employees", "no. of employees", "headcount", "size"],
    "budget": ["budget"],
    "timeline": ["timeline", "timeframe", "time frame", "buying timeline"],
    "submitted_at": ["scanned at", "scan date", "scan time", "date", "timestamp"],
}
_ALIAS_LOOKUP = {alias: field for field, aliases in HEADER_ALIASES.items() for alias in aliases}


class SimulatedCrash(RuntimeError):
    """Raised by the fault-injection hook to emulate the process dying mid-transaction."""


class BatchError(Exception):
    pass


def _norm_header(h):
    return re.sub(r"\s+", " ", re.sub(r"[_\-]+", " ", (h or "").replace("﻿", ""))).strip().lower()


def map_headers(headers):
    mapping = {}
    for i, h in enumerate(headers):
        field = _ALIAS_LOOKUP.get(_norm_header(h))
        if field and field not in mapping.values():
            mapping[i] = field
    return mapping


def _parse_timestamp(value):
    value = (value or "").strip()
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y", "%Y-%m-%d", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return datetime.strptime(value, fmt).strftime("%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            continue
    return None


def read_csv(path):
    """Return (file_hash, headers, rows, warnings). rows = list of (line_no, cells)."""
    with open(path, "rb") as fh:
        blob = fh.read()
    file_hash = hashlib.sha256(blob).hexdigest()
    warnings = []
    if b"\x00" in blob:
        warnings.append("stripped %d NUL byte(s)" % blob.count(b"\x00"))
        blob = blob.replace(b"\x00", b"")
    try:
        text = blob.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = blob.decode("cp1252", errors="replace")
        warnings.append("file is not UTF-8; decoded as Windows-1252")
    reader = csv.reader(io.StringIO(text, newline=""))
    try:
        headers = next(reader)
    except StopIteration:
        raise BatchError("CSV file is empty")
    rows = []
    for cells in reader:
        if not any(c.strip() for c in cells):
            continue  # blank line
        rows.append((reader.line_num, cells))
    return file_hash, headers, rows, warnings


def row_to_raw(cells, mapping):
    raw = {}
    for i, field in mapping.items():
        value = cells[i] if i < len(cells) else ""
        raw[field] = value
    full = raw.pop("full_name", None)
    if full and not raw.get("first_name") and not raw.get("last_name"):
        parts = full.strip().split(" ", 1)
        raw["first_name"] = parts[0]
        raw["last_name"] = parts[1] if len(parts) > 1 else ""
    if "submitted_at" in raw:
        raw["submitted_at"] = _parse_timestamp(raw["submitted_at"])
    raw["form"] = "tradeshow"
    return raw


def run_batch(conn, path, config, crash_after=None, source="tradeshow", crash_mode="exception"):
    """Import one CSV. ``crash_after=N`` injects a crash while processing the (N+1)-th new row,
    *after* its writes were issued but before COMMIT (used by tests and the failure demo).
    crash_mode "exception" raises SimulatedCrash (rollback runs); "kill" calls os._exit(137) -
    the process dies on the spot like kill -9 / power loss: no rollback code, no cleanup, the
    open SQLite transaction is simply never committed."""
    file_hash, headers, rows, warnings = read_csv(path)
    mapping = map_headers(headers)
    if "email" not in mapping.values():
        raise BatchError("no email column found in headers %r" % headers)

    # a previous run of this exact file that never finished (process killed) is marked as such
    conn.execute("UPDATE batch_runs SET status='interrupted' WHERE file_hash=? AND status='running'", (file_hash,))
    cur = conn.execute("INSERT INTO batch_runs (file_name, file_hash, status, rows_total) VALUES (?,?,?,?)",
                       (os.path.basename(path), file_hash, "running", len(rows)))
    run_id = cur.lastrowid
    stats = {"rows": len(rows), "created": 0, "merged": 0, "rejected": 0, "duplicate_rows_in_file": 0,
             "already_processed": 0, "warnings": warnings,
             "unmapped_headers": [h for i, h in enumerate(headers) if i not in mapping]}
    seen = set()
    processed_new = 0

    def crash_hook(_result):
        if crash_mode == "kill":
            os._exit(137)
        raise SimulatedCrash("injected crash while committing row %d" % (processed_new + 1))

    try:
        for line_no, cells in rows:
            row_hash = hashlib.sha256(json.dumps([c.strip().lower() for c in cells]).encode("utf-8")).hexdigest()
            key = "csv:%s:%s" % (file_hash[:12], row_hash[:16])
            if key in seen:
                stats["duplicate_rows_in_file"] += 1
                continue
            seen.add(key)
            if store.key_exists(conn, key):
                stats["already_processed"] += 1
                continue
            hook = crash_hook if crash_after is not None and processed_new >= crash_after else None
            if len(cells) != len(headers):
                raw = {"_line": line_no, "_cells": cells}
                result = _reject_row(conn, key, source, raw,
                                     "corrupt_row: expected %d columns, got %d" % (len(headers), len(cells)), hook)
            else:
                raw = row_to_raw(cells, mapping)
                result = pipeline.ingest(conn, raw, source, key, config, fault_hook=hook)
            stats[result["status"]] = stats.get(result["status"], 0) + 1
            processed_new += 1
    except BaseException as exc:
        conn.execute("UPDATE batch_runs SET status='crashed', stats=?, finished_at=strftime('%Y-%m-%dT%H:%M:%SZ','now') "
                     "WHERE run_id=?", (json.dumps(dict(stats, error=str(exc))), run_id))
        raise
    conn.execute("UPDATE batch_runs SET status='completed', stats=?, finished_at=strftime('%Y-%m-%dT%H:%M:%SZ','now') "
                 "WHERE run_id=?", (json.dumps(stats), run_id))
    stats["run_id"] = run_id
    return stats


def _reject_row(conn, key, source, raw, reason, hook):
    conn.execute("BEGIN IMMEDIATE")
    try:
        result = pipeline._reject(conn, key, source, raw, reason, [])
        if hook:
            hook(result)
        conn.execute("COMMIT")
        return result
    except BaseException:
        conn.execute("ROLLBACK")
        raise
