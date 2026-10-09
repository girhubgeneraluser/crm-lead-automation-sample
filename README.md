# leadflow: unified lead intake for a small B2B team

> **Sample project — synthetic data, not client work.**
> Built for a portfolio against a fictional brief ([BRIEF.md](BRIEF.md)). "Northbeam Consulting",
> its sales reps, and every lead, company, domain and phone number in here are generated.
> UK numbers use Ofcom's drama range (07700 900xxx), North American ones the 555-01xx fictional range.

## Delivery metrics

| Metric | Value |
|---|---|
| Wall-clock start (`date`) | 2026-10-09 03:05:45 +04 |
| Wall-clock end (`date`) | 2026-10-09 03:25:02 +04 |
| QA/fix cycles | 3 (listed below) |
| Automated tests | 77 `unittest` tests, all passing (about 13 s) |
| Runtime dependencies | none. Python 3.9.6 standard library only |
| Demo volume | 294 inbound records (180 webhook deliveries + 114 CSV rows) became 230 HubSpot contacts |

QA/fix cycles:
1. **Demo output review.** A Canadian company (`.ca` domain) came out as "United States" because the
   country was taken from the shared `+1` calling code first. The ccTLD is now checked first.
   I also fixed `gmbh.` / `Inc..` company suffix casing, shortened HTTP rejection reasons so the
   report groups them, and replaced Canadian area codes in the US phone generator.
2. **First test run: 1 failure out of 76.** Mixed-case input such as `ada LOVELACE` was left
   as typed. Name casing is now fixed one token at a time, so `DeShawn` and `McKay` are still
   left alone. The same run showed `ResourceWarning`s from unclosed files in tests.
3. **Failure-injection review.** The first crash demo used only the CSV, which produces no
   hot leads, so the outbox was empty and proved nothing about duplicate pings. The demo now
   runs on the full pipeline (webhooks, then CSV). I added a real hard-kill mode
   (`os._exit(137)` inside the open transaction) and a subprocess test for it. Runs left in
   `running` by a killed process are now marked `interrupted`.

---

## What it does

Three lead sources go through one pipeline:

```
 Website form ──POST /webhooks/website──┐
 Typeform ─────POST /webhooks/typeform──┤  HMAC check -> JSON Schema -> adapter
 Trade-show CSV ──import-csv────────────┘  tolerant CSV parse -> header aliases
                                            │
                                            ▼
     ┌──────────────── one SQLite transaction per record ────────────────┐
     │ idempotency key seen? ──yes──> "duplicate" (nothing re-processed) │
     │ normalise (email, E.164 phone, names, company, country)           │
     │ invalid email ──> quarantined (rejects.csv)                       │
     │ dedupe: exact email (incl. merged alt emails) -> fuzzy co.+name   │
     │ merge (documented rules) or create                                │
     │ score (JSON rules) -> tier hot/warm/cold (never downgrades)       │
     │ route new leads: account stickiness -> territory -> round-robin   │
     │ first time hot -> Slack payload into notifications table          │
     │ ledger row (key, outcome, fixes, raw)                     COMMIT  │
     └───────────────────────────────────────────────────────────────────┘
                                            │
                                            ▼  export (regenerated from DB, atomic writes)
     out/hubspot_contacts.csv   out/slack_outbox.jsonl   out/rejects.csv   out/run_report.md
```

How each requirement in the brief is covered:

| Requirement | Where | How |
|---|---|---|
| Webhook intake with HMAC-SHA256 | `leadflow/server.py` | `X-Leadflow-Signature: sha256=hex(HMAC(secret, "{ts}." + raw_body))`, constant-time compare, 5-minute timestamp window (replay protection). Checked on the raw bytes before parsing. |
| Never double-process a webhook | `pipeline.ingest`, `store.py` | The event id goes into the ledger in the same transaction as the lead write. `BEGIN IMMEDIATE` serialises concurrent retries: 12 parallel copies of one event give 1 `created` and 11 `duplicate` (tested). |
| Clear 4xx errors | `server.py`, `schema.py` | 400 bad JSON, 401 signature/timestamp, 404, 405, 411, 413, 415, 422 with every schema error and its JSON path (e.g. `$.lead.email is required`). |
| Messy trade-show CSV | `leadflow/batch.py` | Accepts UTF-8 with or without BOM, with Windows-1252 fallback. Strips NUL bytes, matches header aliases (`E-Mail Address`, `Surname`, ...), drops identical duplicate rows, and quarantines rows with the wrong column count instead of guessing. |
| Normalisation | `leadflow/normalize.py` | Lowercases and validates email. Converts phones to E.164 for 14 countries; if the country is unknown the raw value is kept and flagged. Fixes name casing (`O'Brien`, `McAllister`, `van der Berg`). Fills a missing company from the email domain, but never from free-mail domains, and handles `co.uk` / `com.au`. Infers country from the ccTLD, then from the phone. |
| Dedupe + merge rules | `leadflow/dedupe.py` (rules in docstring and below) | Exact email first, including emails merged earlier. Then fuzzy company + name. |
| Scoring | `config/leadflow.json` → `scoring` | Rules use the ops `eq`, `ne`, `in`, `contains_any`, `gte`, `lte`, `between`, `present`. Thresholds are hot ≥ 70 and warm ≥ 40. The config is validated at startup. |
| Routing | `leadflow/routing.py` + `routing` config | Account stickiness, then territory by ISO country, then round-robin over active reps. The pointer is stored in SQLite and advanced inside the lead transaction. |
| HubSpot import CSV | `out/hubspot_contacts.csv` | `First Name, Last Name, Email, Phone Number, Company Name, Lead Status, Contact owner` plus the standard `Job Title`, `Country/Region`. Contact owner is the rep's **email**, which is what HubSpot's importer matches owners on. Lead Status values are HubSpot defaults (`New`, `Open`). |
| Hot-lead notification | `out/slack_outbox.jsonl` | One Slack Block Kit payload per line (`text` fallback + header/section/context blocks, owner `<@SLACKID>` mention). Limited to one per lead by `notifications.lead_id` PRIMARY KEY. No network calls. |
| Run summary | `out/run_report.md` | Inbound by source, rejects by reason, refused webhook requests, replays, data-quality fixes, routing table, hot leads, batch-run history. |

### Dedupe and merge rules

**Matching** (the first match wins):
1. **Exact email.** The incoming email (lowercased) equals the primary email *or any previously
   merged email* of an existing contact.
2. **Fuzzy company + name.** Same normalised company (`ACME Ltd.` == `Acme Limited`) **or** same
   corporate email domain, **and** a full-name similarity of at least 0.88 (difflib). `Jon Smith`
   matches `John Smith`; `Mark Jones` does not match `Mike Jones`. Guard rail: two *different
   corporate mailboxes* only merge if their local parts are also similar (≥ 0.85). A shared
   mailbox like `research-desk@` is never folded into a person.

**Merging** an incoming record into an existing contact:
- **Primary email.** The existing primary email stays, unless it is free-mail and the incoming
  one is corporate (the work address wins). All addresses are kept, so a later submission from
  the old Gmail address still matches.
- **Empty fields** are always filled from the incoming record.
- **Phone.** A valid E.164 number beats an unparseable one; otherwise the most recent wins.
- **Job title, company size, budget, timeline.** The most recent submission wins. An *older*
  record that arrives late does not overwrite newer values.
- **First name, last name, country.** The first-touch value is kept, because reps already know
  the contact by it.
- **Company.** An explicitly typed name beats one inferred from the email domain.
- **Form.** `demo_request` is sticky (it is the strongest intent signal).
- **Sources.** Sources are unioned and `touch_count` is the number of distinct sources, which
  feeds the `multi_touch` scoring rule. `first_seen` and `last_seen` are tracked.
- **Owner and tier.** The owner is **never reassigned** by a merge, and the tier **never
  downgrades**, so a hot lead can't quietly drop out of a rep's queue.

## How to run

Requirements: macOS/Linux with `python3` ≥ 3.9. Nothing to install. Run from this folder:

```sh
cd portfolio/crm-lead-automation

# 1. full test suite (77 tests, ~13 s)
python3 -m unittest discover -s tests -v

# 2. full end-to-end demo on fresh state:
#    generate ~300 synthetic records -> start the real HTTP server on 127.0.0.1 ->
#    POST every webhook signed (incl. replays, bad signatures, malformed bodies) ->
#    import the messy trade-show CSV -> write all outputs to out/
python3 -m leadflow demo

# 3. failure injection: hard-kill a CSV import mid-transaction, re-run, compare with a clean run
sh scripts/failure_demo.sh
```

Individual commands (all accept `--db PATH`, `--out DIR`, `--config PATH`):

```sh
python3 -m leadflow generate                              # data/webhook_events.jsonl, data/tradeshow_leads.csv
LEADFLOW_WEBHOOK_SECRET=change-me python3 -m leadflow serve --port 8787
python3 -m leadflow deliver-webhooks data/webhook_events.jsonl   # in-process server + signed POSTs
python3 -m leadflow import-csv data/tradeshow_leads.csv   # resumable; --crash-after N [--crash-mode kill|exception]
python3 -m leadflow status                                # counts + batch run history
python3 -m leadflow export                                # regenerate everything in out/
```

Sending one signed request by hand to a running `serve` (verified working; replaying it returns
`duplicate`, and a wrong signature returns 401):

```sh
export LEADFLOW_WEBHOOK_SECRET=local-dev-secret
BODY='{"event_id":"wev_manual0001","submitted_at":"2026-10-01T09:00:00Z","form":"demo_request","lead":{"first_name":"test","last_name":"USER","email":" Test.User@Example-Widgets.co.uk ","phone":"07700 900001","job_title":"head of operations","company_size":250}}'
TS=$(date +%s)
SIG="sha256=$(printf '%s.%s' "$TS" "$BODY" | openssl dgst -sha256 -hmac "$LEADFLOW_WEBHOOK_SECRET" -hex | sed 's/^.* //')"
curl -s -X POST http://127.0.0.1:8787/webhooks/website -H 'Content-Type: application/json' \
     -H "X-Leadflow-Timestamp: $TS" -H "X-Leadflow-Signature: $SIG" -d "$BODY"
# {"status": "created", ..., "tier": "hot", "score": 75, "owner": "amelia.hart@northbeam.example", "notified": true}
```

## Project layout

```
BRIEF.md                    synthetic client brief
config/leadflow.json        scoring rules, thresholds, territories, reps, HubSpot columns, webhook limits
config/schemas/*.json       JSON Schemas: website, typeform, canonical lead
leadflow/server.py          http.server webhook endpoint (HMAC, timestamp window, 4xx mapping)
leadflow/schema.py          minimal JSON Schema validator (all errors, JSON paths)
leadflow/pipeline.py        source adapters + the single transactional ingest()
leadflow/normalize.py       email / phone E.164 / names / company / country
leadflow/dedupe.py          matching + merge rules
leadflow/scoring.py         config-driven rules engine
leadflow/routing.py         stickiness, territory, persistent round-robin
leadflow/notify.py          Slack Block Kit payload
leadflow/batch.py           tolerant CSV reader, resumable batch, fault injection
leadflow/outputs.py         HubSpot CSV, outbox, rejects, markdown report (atomic writes)
leadflow/store.py           SQLite schema (WAL) and helpers
leadflow/generator.py       deterministic synthetic data with deliberate defects
leadflow/demo.py            end-to-end demo driver (real HTTP on 127.0.0.1)
scripts/failure_demo.sh     crash + recovery demonstration
tests/                      77 unittest tests
data/  out/  var/           generated inputs, outputs, SQLite state (all reproducible)
```

## Demo results (actual output of `python3 -m leadflow demo`)

```
[1/4] generated synthetic data: {"cross_source_email_duplicates": 25, "csv_bad_emails": 6, "csv_corrupt_rows": 2, "csv_exact_duplicates": 6, "csv_rows": 114, "fuzzy_duplicates": 10, "people": 230, "total_inbound_records": 294, "webhook_broken": 7, "webhook_deliveries": 180, "webhook_ok": 169, "webhook_replays": 4}
[2/4] webhook deliveries via http://127.0.0.1:52661 -> {"200 created": 160, "200 duplicate": 4, "200 merged": 5, "200 rejected": 4, "400 body is not valid JSON": 1, "401 unauthorized: signature mismatch": 2, "401 unauthorized: timestamp outside tolerance window": 1, "422 payload failed schema validation": 3}
[3/4] trade-show CSV batch -> {"already_processed": 0, "created": 70, "duplicate_rows_in_file": 6, "merged": 30, "rejected": 8, "rows": 114, "run_id": 1, "unmapped_headers": ["Notes"], "warnings": ["stripped 1 NUL byte(s)"]}
[4/4] exports written to .../out -> {"hubspot_contacts": 230, "rejects": 12, "slack_notifications": 22}
```

The generator created 230 synthetic people and the pipeline produced **exactly 230 contacts**:
every planted duplicate was found (25 by exact email across sources, 10 fuzzy free-mail plus
name-typo pairs, 6 identical CSV rows, 4 webhook replays) and no distinct people were merged.
Headline from [`out/run_report.md`](out/run_report.md):

- 230 unique contacts ready for HubSpot import (from 277 processed records).
- 22 hot, 132 warm, 76 cold.
- 35 records merged (25 exact email, 10 fuzzy company+name).
- 12 records rejected (8 invalid email, 2 corrupt rows, 1 missing, 1 placeholder), 4 replays
  ignored, 7 webhook requests refused (2 bad signature, 1 stale timestamp, 1 malformed JSON,
  3 schema errors).

Sample HubSpot rows (`out/hubspot_contacts.csv`):

```
First Name,Last Name,Email,Phone Number,Company Name,Lead Status,Contact owner,Job Title,Country/Region
Leila,Ramsay,leila.ramsay@thistlebrook-media.co.uk,+447700900795,Thistlebrook Media Ltd,Open,amelia.hart@northbeam.example,Head of Sales,United Kingdom
Freya,van der Berg,freya.vanderberg@saltmarsh-robotics.de,,Saltmarsh Robotics AG,New,jonas.keller@northbeam.example,Analyst,Germany
```

## Test results

77 tests in `tests/`:

| File | Covers |
|---|---|
| `test_server.py` (22) | valid signature; **bad signature**, wrong secret, body modified after signing, missing header, stale timestamp → 401; **replayed event** gives `duplicate`, no second lead or Slack ping; 12 concurrent replays → exactly one processed; **malformed payload** → 400; schema → 422 with paths; resend after fixing a 422; Typeform mapping; invalid email quarantined; 413/415/404; audit of refusals |
| `test_normalize.py` (17) | **invalid emails** (10 kinds), placeholders, legal-but-odd addresses; UK/US/DE/IE phone formats → E.164; name casing; company suffixes and keys; free-mail exclusion; `co.uk`; country aliases; `.ca` beats `+1` |
| `test_dedupe.py` (8) | **duplicates across 3 sources**; fuzzy free-mail + typo merge; old alternate email still matches; Mark vs Mike Jones not merged; shared mailbox not merged; every merge rule; owner sticky; tier never downgrades |
| `test_scoring_routing.py` (12) | **scoring thresholds** at 69/70 and 39/40; clamping; every op; config-only changes; invalid config detected; **routing fairness** (31 leads over 2 reps, 30 over 3 reps across a restart); inactive rep skipped; territories; account stickiness on and off |
| `test_batch_outputs.py` (14) | messy CSV end to end; **re-running a batch is a no-op**; cp1252 + NUL; missing email column; **crash mid-row → rollback → resume**; **hard kill (`os._exit`) via the CLI → resume**; **crash + resume produces byte-identical outputs to a clean run on the 294-record data set**; **HubSpot CSV header correctness**; HubSpot values and status vocabulary; Slack payload shape and limits; deterministic, atomic exports; report sections; generator volume, defects and determinism |
| `test_schema.py` (4) | validator types, all errors collected, nested array paths |

Tail of `python3 -m unittest discover -s tests -v`:

```
test_typeform_happy_path_maps_answers (test_server.WebhookServerTests) ... ok
test_unknown_endpoint_404 (test_server.WebhookServerTests) ... ok
test_valid_signature_creates_lead (test_server.WebhookServerTests) ... ok
test_verify_signature_unit (test_server.WebhookServerTests) ... ok
test_wrong_content_type_415 (test_server.WebhookServerTests) ... ok
test_wrong_secret_rejected (test_server.WebhookServerTests) ... ok

----------------------------------------------------------------------
Ran 77 tests in 13.391s

OK
```

## Failure injection and recovery

Two failure scenarios are built into the demo data and the tooling.

### 1. Corrupted CSV rows (always present in the generated file)

The trade-show export contains a row with a stray extra column, a truncated row, and a NUL byte
inside a name. The NUL byte would normally make Python's `csv` module abort the whole file. The
batch removes the NUL and records a warning. The two malformed rows are quarantined with the
reason and raw cells, and the other 106 unique rows import normally:

```
tradeshow,csv:820b4ccd8938:9bfe6a0ac8dcd9ff,"corrupt_row: expected 10 columns, got 11","{""_cells"": [""LEILA"", ""GALLAGHER"", ...
tradeshow,csv:820b4ccd8938:0d61169772d143e6,"corrupt_row: expected 10 columns, got 4","{""_cells"": [""fiona"", ""MOREAU"", ...
```

### 2. Process killed mid-import (`sh scripts/failure_demo.sh`)

The script does the following:
1. Builds a reference database: webhooks, then a clean CSV import.
2. Builds a second database from the same webhooks, then imports the CSV with
   `--crash-after 50 --crash-mode kill`. While row 51 is being written (lead merged, routing
   pointer advanced, ledger row inserted, **COMMIT not yet issued**), the process calls
   `os._exit(137)`, the same as `kill -9` or a power cut: no `except`, no `finally`, no rollback
   code runs.
3. Re-runs the same command, then runs it once more, and compares the exports byte for byte.

Actual output:

```
== 1. same webhooks, then a CSV import that is hard-killed (os._exit(137), no cleanup) mid-transaction on row 51
  "leads": 160,
  "ledger_entries": 169,
  "hot_notifications": 19,
exit code: 137
== 2. state after the crash: 50 rows committed, row 51 never committed, batch run left in 'running'
{
  "leads": 193,
  "ledger_entries": 219,
  "hot_notifications": 21,
  "batch_runs": [ { "run_id": 1, "file_name": "tradeshow_leads.csv", "status": "running", "rows_total": 114 } ]
}
== 3. recovery = run the exact same command again
{ "already_processed": 50, "created": 37, "duplicate_rows_in_file": 6, "merged": 16, "rejected": 5, "rows": 114, "run_id": 2, ... }
exit code: 0
== 4. and once more (must be a no-op)
  "already_processed": 108,
  "created": 0,
  "merged": 0,
  "rejected": 0,
== 4b. batch run history
      "run_id": 1,	      "status": "interrupted",
      "run_id": 2,	      "status": "completed",
      "run_id": 3,	      "status": "completed",
== 5. compare exports: recovered vs clean
 231 lines  IDENTICAL  hubspot_contacts.csv  06423b66880f1683
  22 lines  IDENTICAL  slack_outbox.jsonl  56942d0ae59bed2d
  13 lines  IDENTICAL  rejects.csv  5c468ce08ca5fc15
```

(169 webhook ledger entries + 50 CSV rows = 219.) The 50 committed rows were skipped, and
row 51, which was never committed, was processed from scratch. The round-robin pointer was not
advanced twice and no Slack ping was duplicated. The recovered outputs are **byte-identical**
to the run that never crashed. The run is idempotent because each record is one transaction
and the ledger key is written inside it. Exports are rebuilt from the database and written via
temp file + `os.replace`, so they are never half-written.

## Limitations

- **Signature scheme.** The scheme is a generic `X-Leadflow-Signature` HMAC. Real Typeform
  signs with `Typeform-Signature: sha256=<base64>` over the body only. That would be a small
  per-source verifier and is not implemented here. Secrets come from an env var with no
  rotation or multiple active keys.
- **Webhook rejects.** A webhook with an invalid email returns `200 {"status":"rejected"}` and
  is quarantined. This is deliberate: the sender cannot fix the data by retrying.
  Schema-invalid payloads get a 422 and are *not* recorded, so a corrected resend with the same
  event id is processed.
- **Phone handling** is a hand-written plan for 14 countries, not libphonenumber. It doesn't
  validate number ranges or mobile vs landline, and `+1` maps to US unless the domain says
  Canada.
- **Emails.** Validation is syntactic only: no MX/SMTP checks. Typo domains such as
  `gmial.com` are not auto-corrected (that's risky). International (IDN) domains are rejected.
- **Fuzzy matching** compares within the same company only (difflib, O(n) per company), so it
  will not catch a person who changed employer. At larger volumes it needs blocking or a proper
  matcher, plus a human review queue for borderline scores (0.80–0.88).
- **Free-mail list.** Company inference skips free-mail domains from a fixed list in the
  config. Uncommon ISP domains will be treated as corporate.
- **Delivery.** There is no Slack delivery worker. The outbox is the hand-off point, and a real
  sender would need retry/backoff and a delivered-at column.
- **Concurrency.** It is single-node: SQLite with WAL plus `BEGIN IMMEDIATE` serialises writes,
  which is fine for a few requests per second.
- **CSV.** It is read into memory, which is fine for trade-show sized files (thousands of rows),
  not millions.
- **HubSpot.** Only the CSV is produced; no API push. `Contact owner` must match an existing
  HubSpot user email, and the extra columns (`Job Title`, `Country/Region`) are standard
  properties but can be removed in config.
- **Generated data** is synthetic and well-behaved compared with real exports: no multi-line
  notes fields and no unbalanced quotes that swallow rows.

## How this would be adapted for a real client

1. **Discovery (half a day).** Get real samples of each source: the website form payload, a
   Typeform webhook from their account, and the last two badge-scanner exports. Get the HubSpot
   owner list, existing Lead Status values and custom properties, and territory rules written
   down by the sales lead, plus the scoring criteria they already use informally.
2. **Source verifiers and adapters.** Implement Typeform's native signature (base64 HMAC,
   `Typeform-Signature`) and whatever the website form or CMS sends. Map their actual Typeform
   field refs in `adapt_typeform`. Add header aliases for their scanner vendor.
3. **Config, not code.** Translate their scoring and territories into `config/leadflow.json`
   and walk the sales lead through changing a threshold themselves. Add a `--dry-run` scoring
   report on last quarter's leads so they can calibrate the hot threshold before go-live.
4. **HubSpot.** Either keep the CSV workflow (weekly import), or replace the export with the
   CRM Contacts API (batch upsert by email, owner by `hubspot_owner_id`), reusing the same
   ledger for idempotency. Seed the dedupe store with an export of existing HubSpot contacts
   first, so we never create a duplicate of a contact they already have.
5. **Slack.** Add a small outbox worker that posts to their incoming webhook with retry,
   backoff and a `delivered_at` column (an outbox row is never deleted, so a ping is never
   lost or sent twice).
6. **Hosting.** A small VM or container behind HTTPS (Caddy or nginx) with the secret in a
   secrets manager, nightly SQLite backup (`.backup`), and `/healthz` monitoring. If volume
   or availability needs grow, move the store to Postgres and keep the transaction design
   (`INSERT ... ON CONFLICT DO NOTHING` on the ledger).
7. **Data protection.** The client is UK/EU-facing, so we would document the lawful basis,
   set retention for `rejects.csv` and raw payloads in the ledger (auto-purge after N days),
   and add a delete-by-email command for erasure requests.
8. **Handover.** Run the test suite in their CI, add a runbook ("import died → re-run the same
   command"), and spend one session with the ops lead on reading `run_report.md`.
