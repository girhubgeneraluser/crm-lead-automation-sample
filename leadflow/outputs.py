"""Exports: HubSpot contact-import CSV, Slack outbox, rejects file, markdown run report.

Every export is regenerated from the database (the source of truth) and written atomically
(temp file + os.replace), so exporting twice - or after a crash - produces identical files and
never a half-written one.
"""
import collections
import csv
import io
import json
import os
from datetime import datetime, timezone

from . import store
from .normalize import COUNTRY_NAMES
from .notify import SAMPLE_LABEL


def _atomic_write(path, text):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def hubspot_rows(leads, config):
    status_map = config["hubspot"]["lead_status_by_tier"]
    for lead in leads:
        values = {
            "First Name": lead.get("first_name") or "",
            "Last Name": lead.get("last_name") or "",
            "Email": lead["email"],
            "Phone Number": lead.get("phone") or "",
            "Company Name": lead.get("company") or "",
            "Lead Status": status_map[lead["tier"]],
            "Contact owner": lead.get("owner") or "",
            "Job Title": lead.get("job_title") or "",
            "Country/Region": COUNTRY_NAMES.get(lead.get("country"), lead.get("country") or ""),
        }
        yield [values[c] for c in config["hubspot"]["columns"]]


def export_hubspot_csv(conn, path, config):
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\r\n")
    writer.writerow(config["hubspot"]["columns"])
    n = 0
    for row in hubspot_rows(store.all_leads(conn), config):
        writer.writerow(row)
        n += 1
    _atomic_write(path, buf.getvalue())
    return n


def export_slack_outbox(conn, path):
    lines = [r["payload"] for r in conn.execute("SELECT payload FROM notifications ORDER BY lead_id")]
    _atomic_write(path, "".join(line + "\n" for line in lines))
    return len(lines)


def export_rejects(conn, path):
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\r\n")
    writer.writerow(["source", "idempotency_key", "reason", "raw"])
    n = 0
    for r in conn.execute("SELECT source, key, reason, raw FROM ingest_log WHERE outcome='rejected' ORDER BY seq"):
        writer.writerow([r["source"], r["key"], r["reason"], r["raw"]])
        n += 1
    _atomic_write(path, buf.getvalue())
    return n


def _table(headers, rows):
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    out += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return "\n".join(out)


def build_report(conn, config, generated_at=None):
    generated_at = generated_at or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    log = conn.execute("SELECT source, outcome, reason, match_type, fixes FROM ingest_log").fetchall()
    leads = store.all_leads(conn)

    by_source = collections.OrderedDict()
    for r in log:
        s = by_source.setdefault(r["source"], collections.Counter())
        s[r["outcome"]] += 1
    reject_reasons = collections.Counter(
        (r["reason"].split(":")[0] if r["reason"] else "unknown") for r in log if r["outcome"] == "rejected")
    match_types = collections.Counter(r["match_type"] for r in log if r["outcome"] == "merged")
    fixes = collections.Counter()
    for r in log:
        for f in json.loads(r["fixes"] or "[]"):
            fixes[f] += 1
    replays = conn.execute("SELECT source, COUNT(*) n FROM replay_log GROUP BY source ORDER BY source").fetchall()
    http_rej = conn.execute("SELECT status, reason, COUNT(*) n FROM http_rejections GROUP BY status, reason "
                            "ORDER BY status, reason").fetchall()
    runs = conn.execute("SELECT run_id, file_name, status, rows_total, stats FROM batch_runs ORDER BY run_id").fetchall()
    tiers = collections.Counter(l["tier"] for l in leads)
    owners = collections.Counter((l.get("territory"), l.get("owner"), l.get("routing_method")) for l in leads)
    multi = sum(1 for l in leads if l.get("touch_count", 1) > 1)
    reps = config["routing"]["reps"]

    md = ["# Lead intake run report", "",
          "> %s." % SAMPLE_LABEL, "",
          "Generated: %s" % generated_at, "",
          "## Headline", "",
          "- **%d unique contacts** ready for HubSpot import (from %d processed records)." % (len(leads), len(log)),
          "- **%d hot**, %d warm, %d cold." % (tiers["hot"], tiers["warm"], tiers["cold"]),
          "- **%d records merged** into existing contacts (%d exact email, %d fuzzy company+name); %d contacts were seen in more than one source." % (
              sum(match_types.values()), match_types["email"], match_types["fuzzy"], multi),
          "- **%d records rejected** (see `rejects.csv`), %d webhook replays ignored, %d webhook requests refused at the door." % (
              sum(reject_reasons.values()), sum(r["n"] for r in replays), sum(r["n"] for r in http_rej)),
          "", "## Inbound by source", "",
          _table(["Source", "Created", "Merged", "Rejected", "Total"],
                 [[s, c["created"], c["merged"], c["rejected"], sum(c.values())] for s, c in by_source.items()]),
          "", "## Rejected records", "",
          _table(["Reason", "Count"], sorted(reject_reasons.items(), key=lambda kv: (-kv[1], kv[0]))) if reject_reasons else "None.",
          "", "## Webhook requests refused (never processed)", "",
          _table(["HTTP status", "Reason", "Count"], [[r["status"], r["reason"], r["n"]] for r in http_rej]) if http_rej else "None.",
          "", "Replayed webhook events acknowledged but not re-processed: " +
          (", ".join("%s %d" % (r["source"], r["n"]) for r in replays) if replays else "none") + ".",
          "", "## Data-quality fixes applied", "",
          _table(["Fix", "Records"], sorted(fixes.items(), key=lambda kv: (-kv[1], kv[0]))) if fixes else "None.",
          "", "## Routing", "",
          _table(["Territory", "Owner", "Method", "Contacts"],
                 [[t, "%s (%s)" % (reps.get(o, {}).get("name", o), o), m, n]
                  for (t, o, m), n in sorted(owners.items(), key=lambda kv: (str(kv[0][0]), str(kv[0][1]), str(kv[0][2])))]),
          "", "## Hot leads", "",
          _table(["#", "Name", "Company", "Title", "Score", "Owner", "Sources"],
                 [[l["id"], ("%s %s" % (l.get("first_name") or "", l.get("last_name") or "")).strip(), l.get("company") or "",
                   l.get("job_title") or "", l["score"], reps.get(l.get("owner"), {}).get("name", l.get("owner")),
                   ", ".join(l.get("sources", []))] for l in leads if l["tier"] == "hot"]),
          "", "## CSV batch runs", "",
          _table(["Run", "File", "Status", "Rows", "Created", "Merged", "Rejected", "Dup rows", "Already done"],
                 [[r["run_id"], r["file_name"], r["status"], r["rows_total"]] +
                  [json.loads(r["stats"] or "{}").get(k, "") for k in ("created", "merged", "rejected", "duplicate_rows_in_file", "already_processed")]
                  for r in runs]) if runs else "No CSV batches.",
          ""]
    return "\n".join(md)


def export_all(conn, out_dir, config, generated_at=None):
    counts = {
        "hubspot_contacts": export_hubspot_csv(conn, os.path.join(out_dir, "hubspot_contacts.csv"), config),
        "slack_notifications": export_slack_outbox(conn, os.path.join(out_dir, "slack_outbox.jsonl")),
        "rejects": export_rejects(conn, os.path.join(out_dir, "rejects.csv")),
    }
    _atomic_write(os.path.join(out_dir, "run_report.md"), build_report(conn, config, generated_at))
    return counts
