"""The single intake path every source goes through.

    adapter (source-specific) -> canonical schema check -> normalise -> dedupe/merge
    -> score -> route (new leads only) -> hot-lead notification -> ledger entry -> COMMIT

``ingest`` runs all of that in ONE SQLite write transaction keyed by an idempotency key
(webhook event id, or file+row hash for CSV rows). Replaying the same key is a no-op.
"""
import json
import os

from . import dedupe, notify, routing, scoring, store
from .normalize import normalize_lead
from .schema import load_schema, validate

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_CONFIG = os.path.join(ROOT, "config", "leadflow.json")

TIER_RANK = {"cold": 0, "warm": 1, "hot": 2}


class ConfigError(Exception):
    pass


def load_config(path=None):
    with open(path or DEFAULT_CONFIG, encoding="utf-8") as fh:
        cfg = json.load(fh)
    problems = scoring.validate_config(cfg["scoring"])
    reps = cfg["routing"]["reps"]
    for terr in cfg["routing"]["territories"] + [cfg["routing"]["default_territory"]]:
        for rep in terr["reps"]:
            if rep not in reps:
                problems.append("territory %s references unknown rep %s" % (terr["name"], rep))
    if problems:
        raise ConfigError("; ".join(problems))
    cfg["normalisation"]["freemail_domains"] = set(cfg["normalisation"]["freemail_domains"])
    return cfg


# --------------------------------------------------------------------------- adapters

def adapt_website(payload):
    lead = dict(payload["lead"])
    lead["form"] = payload["form"]
    lead["submitted_at"] = payload["submitted_at"]
    return lead


TYPEFORM_REFS = {"first_name", "last_name", "email", "phone", "company", "job_title", "country",
                 "company_size", "budget", "timeline"}


def adapt_typeform(payload):
    resp = payload["form_response"]
    lead = {"form": "pricing_calculator", "submitted_at": resp["submitted_at"]}
    for ans in resp["answers"]:
        ref = ans["field"]["ref"]
        if ref not in TYPEFORM_REFS:
            continue
        kind = ans["type"]
        if kind == "choice":
            value = ans.get("choice", {}).get("label")
        elif kind == "phone_number":
            value = ans.get("phone_number")
        else:
            value = ans.get(kind)
        if ref == "company_size" and isinstance(value, float) and value.is_integer():
            value = int(value)
        lead[ref] = value
    return lead


ADAPTERS = {"website": ("website", adapt_website), "typeform": ("typeform", adapt_typeform)}


# --------------------------------------------------------------------------- ingest

def ingest(conn, raw, source, key, config, fault_hook=None):
    """Process one canonical raw lead. Returns a result dict with ``status`` in
    {created, merged, rejected, duplicate}. ``fault_hook`` (tests / failure demo) is called
    just before COMMIT to simulate a crash after all writes were issued."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        if store.key_exists(conn, key):
            conn.execute("ROLLBACK")
            return {"status": "duplicate", "key": key}

        errors = validate(raw, load_schema("lead"))
        if errors:
            result = _reject(conn, key, source, raw, "schema: " + "; ".join("%s %s" % (e["path"], e["message"]) for e in errors), [])
        else:
            lead, fixes, reason = normalize_lead(raw, config["normalisation"]["freemail_domains"])
            if reason:
                result = _reject(conn, key, source, raw, reason, fixes)
            else:
                result = _upsert(conn, lead, fixes, source, key, raw, config)
        if fault_hook:
            fault_hook(result)
        conn.execute("COMMIT")
        return result
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def _reject(conn, key, source, raw, reason, fixes):
    conn.execute(
        "INSERT INTO ingest_log (key, source, outcome, reason, fixes, raw) VALUES (?,?,?,?,?,?)",
        (key, source, "rejected", reason, json.dumps(fixes), json.dumps(raw, sort_keys=True, default=str)),
    )
    return {"status": "rejected", "key": key, "reason": reason}


def _upsert(conn, lead, fixes, source, key, raw, config):
    match_id, match_type, _sim = dedupe.find_match(conn, lead, config["dedupe"])
    if match_id is not None:
        existing = store.load_lead(conn, match_id)
        record = dedupe.merge(existing, lead, source)
        status = "merged"
    else:
        record = dict(lead)
        record.update({
            "emails": [lead["email"]], "sources": [source], "original_source": source, "touch_count": 1,
            "first_seen": lead.get("submitted_at"), "last_seen": lead.get("submitted_at"),
            "merge_count": 0, "hot_notified": False,
        })
        status = "created"
    record.pop("submitted_at", None)

    score, tier, reasons = scoring.score_lead(record, config["scoring"])
    previous_tier = record.get("tier")
    if previous_tier and TIER_RANK[previous_tier] > TIER_RANK[tier]:
        tier = previous_tier  # never downgrade on merge
    record.update({"score": score, "tier": tier, "score_reasons": reasons})

    if status == "created":
        owner, territory, method = routing.route(conn, record, config["routing"])
        record.update({"owner": owner, "territory": territory, "routing_method": method})
        lead_id = store.insert_lead(conn, record)
    else:
        lead_id = record["id"]
        store.save_lead(conn, record)

    notified = False
    if tier == "hot" and not record.get("hot_notified"):
        record["hot_notified"] = True
        store.save_lead(conn, record)
        payload = notify.build_hot_lead_payload(record, config)
        conn.execute("INSERT OR IGNORE INTO notifications (lead_id, payload) VALUES (?, ?)",
                     (lead_id, json.dumps(payload, sort_keys=True)))
        notified = True

    conn.execute(
        "INSERT INTO ingest_log (key, source, outcome, match_type, lead_id, fixes, raw) VALUES (?,?,?,?,?,?,?)",
        (key, source, status, match_type, lead_id, json.dumps(fixes), json.dumps(raw, sort_keys=True, default=str)),
    )
    return {"status": status, "key": key, "lead_id": lead_id, "match_type": match_type,
            "tier": tier, "score": score, "owner": record.get("owner"), "notified": notified}
