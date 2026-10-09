"""Slack-compatible (incoming-webhook / chat.postMessage Block Kit) payload for hot leads.

No network calls: payloads are stored in the ``notifications`` table (inside the ingest
transaction, keyed by lead id so a lead can only ever produce one hot-lead ping) and exported
to ``out/slack_outbox.jsonl`` for inspection or a later delivery worker.
"""
from .normalize import COUNTRY_NAMES

SAMPLE_LABEL = "Sample project - synthetic data, not client work"


def _md_escape(text):
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def build_hot_lead_payload(lead, config):
    rep = config["routing"]["reps"].get(lead.get("owner"), {})
    owner_ref = "<@%s>" % rep["slack_id"] if rep.get("slack_id") else (lead.get("owner") or "unassigned")
    name = " ".join(p for p in (lead.get("first_name"), lead.get("last_name")) if p) or lead["email"]
    company = lead.get("company") or "(company unknown)"
    title = lead.get("job_title") or "(title unknown)"
    text = "Hot lead: %s, %s at %s - score %d - owner %s" % (name, title, company, lead["score"], owner_ref)
    fields = [
        ("Contact", "%s\n%s" % (_md_escape(name), _md_escape(lead["email"]))),
        ("Company", _md_escape(company)),
        ("Title", _md_escape(title)),
        ("Phone", _md_escape(lead.get("phone") or "-")),
        ("Country", COUNTRY_NAMES.get(lead.get("country"), lead.get("country") or "-")),
        ("Score", "%d (%s)" % (lead["score"], lead["tier"])),
        ("Sources", ", ".join(lead.get("sources", []))),
        ("Owner", "%s (%s)" % (owner_ref, lead.get("territory"))),
    ]
    return {
        "channel": config.get("slack", {}).get("channel"),
        "text": text,
        "blocks": [
            {"type": "header", "text": {"type": "plain_text", "text": "Hot lead: %s" % name[:130]}},
            {"type": "section", "fields": [{"type": "mrkdwn", "text": "*%s*\n%s" % (k, v)} for k, v in fields]},
            {"type": "context", "elements": [{"type": "mrkdwn", "text": "Why: %s | lead #%d | %s" % (
                ", ".join(lead.get("score_reasons", [])), lead["id"], SAMPLE_LABEL)}]},
        ],
    }
