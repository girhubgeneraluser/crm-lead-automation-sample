"""Duplicate detection and merge rules.

Matching (first hit wins)
1. **Exact email** - the incoming email equals the primary *or any previously merged* email
   of an existing lead (case-insensitive; emails are lower-cased during normalisation).
2. **Fuzzy company + name** - an existing lead at the same company (same normalised company
   key, e.g. "ACME Ltd." == "Acme Limited", or same corporate email domain) whose full name is
   similar (difflib ratio >= ``name_similarity_threshold``, default 0.88: "Jon Smith" ~
   "John Smith", but not "Mark Jones" vs "Mike Jones"). Guard rail: two *different corporate
   mailboxes* are only merged when their local parts are also similar (>=0.85), because
   anna.k@acme.com and anne.k@acme.com are more likely two colleagues than one typo.

Merge rules (see ``merge``)
- Primary email: the existing primary, unless it is free-mail and the incoming one is
  corporate (work address wins). All emails are kept in ``emails``.
- Empty fields are always filled from the incoming record.
- Conflicting values: phone -> a valid E.164 number beats an unparseable one, then most recent
  wins; job title / company size / budget / timeline -> most recent submission wins;
  names and country -> first-touch value is kept (reps already know the contact by it).
- Company: an explicitly supplied name beats one inferred from the email domain.
- form: "demo_request" is sticky (strongest intent signal seen).
- sources: union; ``touch_count`` = number of distinct sources; first/last seen tracked.
- Owner is sticky (never reassigned by a merge) and the tier never downgrades.
"""
import difflib
import json

from .normalize import company_key as make_company_key
from .normalize import registrable_domain

LATEST_WINS = ("job_title", "company_size", "budget", "timeline")
FIRST_TOUCH = ("first_name", "last_name", "country")


def _full_name(lead):
    if not lead.get("first_name") or not lead.get("last_name"):
        return None
    return ("%s %s" % (lead["first_name"], lead["last_name"])).lower()


def name_similarity(a, b):
    na, nb = _full_name(a), _full_name(b)
    if not na or not nb:
        return 0.0
    return difflib.SequenceMatcher(None, na, nb).ratio()


def _mailbox_guard_ok(existing, incoming, threshold):
    if existing.get("email_type") == "freemail" or incoming.get("email_type") == "freemail":
        return True
    local_a = existing["email"].split("@")[0]
    local_b = incoming["email"].split("@")[0]
    return difflib.SequenceMatcher(None, local_a, local_b).ratio() >= threshold


def find_match(conn, lead, dedupe_cfg):
    """Return (lead_id, match_type, similarity) or (None, None, None)."""
    row = conn.execute("SELECT lead_id FROM lead_emails WHERE email = ?", (lead["email"],)).fetchone()
    if row:
        return row["lead_id"], "email", 1.0

    clauses, params = [], []
    if lead.get("company_key"):
        clauses.append("company_key = ?")
        params.append(lead["company_key"])
    if lead.get("company_domain"):
        clauses.append("company_domain = ?")
        params.append(lead["company_domain"])
    if not clauses or not _full_name(lead):
        return None, None, None

    best = (None, None, 0.0)
    for r in conn.execute("SELECT id, data FROM leads WHERE " + " OR ".join(clauses) + " ORDER BY id", params):
        existing = json.loads(r["data"])
        sim = name_similarity(existing, lead)
        if sim >= dedupe_cfg["name_similarity_threshold"] and sim > best[2] \
                and _mailbox_guard_ok(existing, lead, dedupe_cfg["email_local_similarity_threshold"]):
            best = (r["id"], "fuzzy", sim)
    return best if best[0] is not None else (None, None, None)


def _newer(incoming, existing):
    return (incoming.get("submitted_at") or "") >= (existing.get("last_seen") or "")


def merge(existing, incoming, source):
    """Return a new dict: ``existing`` updated with ``incoming`` per the documented rules."""
    m = dict(existing)
    newer = _newer(incoming, existing)

    m["emails"] = list(existing.get("emails", [existing["email"]]))
    if incoming["email"] not in m["emails"]:
        m["emails"].append(incoming["email"])
    if existing.get("email_type") == "freemail" and incoming.get("email_type") == "corporate":
        m["email"] = incoming["email"]
        m["email_type"] = "corporate"
        m["company_domain"] = incoming.get("company_domain")

    for f in FIRST_TOUCH:
        if not m.get(f) and incoming.get(f):
            m[f] = incoming[f]
    for f in LATEST_WINS:
        if incoming.get(f) not in (None, "") and (not m.get(f) or newer):
            m[f] = incoming[f]

    if incoming.get("phone"):
        if not m.get("phone") or (incoming["phone_valid"] and not m.get("phone_valid")) \
                or (incoming["phone_valid"] == m.get("phone_valid") and newer):
            m["phone"], m["phone_valid"] = incoming["phone"], incoming["phone_valid"]

    if incoming.get("company"):
        if not m.get("company") or (m.get("company_inferred") and not incoming.get("company_inferred")):
            m["company"] = incoming["company"]
            m["company_inferred"] = incoming.get("company_inferred", False)
    m["company_key"] = make_company_key(m.get("company"))
    if not m.get("company_domain") and m.get("email_type") == "corporate":
        m["company_domain"] = registrable_domain(m["email"].split("@")[1])

    if incoming.get("form") == "demo_request" or not m.get("form"):
        m["form"] = incoming.get("form") or m.get("form")

    m["sources"] = sorted(set(existing.get("sources", [])) | {source})
    m["touch_count"] = len(m["sources"])
    stamps = [s for s in (existing.get("first_seen"), incoming.get("submitted_at")) if s]
    m["first_seen"] = min(stamps) if stamps else None
    stamps = [s for s in (existing.get("last_seen"), incoming.get("submitted_at")) if s]
    m["last_seen"] = max(stamps) if stamps else None
    m["merge_count"] = existing.get("merge_count", 0) + 1
    return m
