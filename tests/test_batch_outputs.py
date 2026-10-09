"""CSV batch path, crash recovery, exports (HubSpot CSV header, Slack payload, report), generator."""
import csv
import hashlib
import json
import os
import shutil
import tempfile
import unittest

from helpers import DBTestCase, config, raw_lead, read_bytes, read_text
from leadflow import batch, generator, outputs, store

HUBSPOT_REQUIRED = ["First Name", "Last Name", "Email", "Phone Number", "Company Name", "Lead Status", "Contact owner"]

MESSY_CSV = (
    "﻿FIRST NAME,Surname,E-Mail Address, Company ,title,Tel,Country,Employees,Scan Date,Notes\r\n"
    "  ada ,LOVELACE, Ada.Lovelace@Analytical-Engines.co.uk ,,head of research,07700 900123,uk,250+,17/09/2026 10:30,hi\r\n"
    "  ada ,LOVELACE, Ada.Lovelace@Analytical-Engines.co.uk ,,head of research,07700 900123,uk,250+,17/09/2026 10:30,hi\r\n"
    "Ada,Lovelace,ada.lovelace@analytical-engines.co.uk,Analytical Engines Ltd,Head of Research,+44 7700 900123,UK,250,2026-09-17 11:00,\r\n"
    "grace,hopper,grace@@cobol.com,Cobol,CTO,(212) 555-0147,usa,40,2026-09-17 11:05,\r\n"
    "linus,berg,linus@kernel-labs.de,Kernel Labs GmbH,CTO,030 1234567,Germany,50-199\r\n"
    "Alan,Turing,alan.t@gmail.com,,Engineer,,,,2026-09-17 12:00,\r\n"
)


class BatchTests(DBTestCase):
    def write(self, name, text, encoding="utf-8"):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding=encoding, newline="") as fh:
            fh.write(text)
        return path

    def test_messy_csv_cleaned_deduped_and_quarantined(self):
        stats = batch.run_batch(self.conn, self.write("show.csv", MESSY_CSV), self.cfg)
        self.assertEqual(stats["rows"], 6)
        self.assertEqual(stats["duplicate_rows_in_file"], 1)            # identical scan
        self.assertEqual((stats["created"], stats["merged"], stats["rejected"]), (2, 1, 2))
        self.assertEqual(stats["unmapped_headers"], ["Notes"])
        ada = store.load_lead(self.conn, 1)
        self.assertEqual((ada["first_name"], ada["last_name"], ada["email"], ada["phone"], ada["company"], ada["country"]),
                         ("Ada", "Lovelace", "ada.lovelace@analytical-engines.co.uk", "+447700900123",
                          "Analytical Engines Ltd", "GB"))
        self.assertEqual(ada["first_seen"], "2026-09-17T10:30:00Z")
        reasons = sorted(r[0] for r in self.conn.execute("SELECT reason FROM ingest_log WHERE outcome='rejected'"))
        self.assertEqual(reasons, ["corrupt_row: expected 10 columns, got 8", "invalid_email"])

    def test_rerun_same_file_is_noop(self):
        path = self.write("show.csv", MESSY_CSV)
        batch.run_batch(self.conn, path, self.cfg)
        before = self.conn.execute("SELECT COUNT(*), SUM(score) FROM leads").fetchone()
        stats = batch.run_batch(self.conn, path, self.cfg)
        self.assertEqual(stats["already_processed"], 5)
        self.assertEqual((stats["created"], stats["merged"], stats["rejected"]), (0, 0, 0))
        self.assertEqual(tuple(self.conn.execute("SELECT COUNT(*), SUM(score) FROM leads").fetchone()), tuple(before))

    def test_cp1252_and_nul_bytes_tolerated(self):
        text = "First Name,Last Name,Email\r\nRenée,Müller,renee@café-ops.de\r\nJo\x00e,Bloggs,joe@bloggs-ltd.co.uk\r\n"
        path = os.path.join(self.tmp, "legacy.csv")
        with open(path, "wb") as fh:
            fh.write(text.encode("cp1252"))
        stats = batch.run_batch(self.conn, path, self.cfg)
        self.assertEqual(len(stats["warnings"]), 2)
        self.assertEqual(stats["created"], 1)                            # joe ok; café domain invalid (IDN)
        self.assertEqual(store.load_lead(self.conn, 1)["first_name"], "Joe")

    def test_missing_email_column_is_a_clear_error(self):
        with self.assertRaises(batch.BatchError):
            batch.run_batch(self.conn, self.write("bad.csv", "Name,Phone\r\nA,1\r\n"), self.cfg)

    def test_crash_mid_row_rolls_back_and_rerun_resumes(self):
        path = self.write("show.csv", MESSY_CSV)
        with self.assertRaises(batch.SimulatedCrash):
            batch.run_batch(self.conn, path, self.cfg, crash_after=2)
        # rows 1-2 committed, row 3 was rolled back entirely
        self.assertEqual(self.count("SELECT COUNT(*) FROM ingest_log"), 2)
        self.assertEqual(self.conn.execute("SELECT status FROM batch_runs").fetchone()[0], "crashed")
        stats = batch.run_batch(self.conn, path, self.cfg)
        self.assertEqual(stats["already_processed"], 2)
        self.assertEqual(self.count("SELECT COUNT(*) FROM ingest_log"), 5)
        self.assertEqual(self.count("SELECT COUNT(*) FROM leads"), 2)


class ExportTests(DBTestCase):
    def setUp(self):
        super().setUp()
        self.ingest(raw_lead(job_title="CEO", company_size=900, form="demo_request"))           # hot
        self.ingest(raw_lead(email="bob@gmail.com", first_name="bob", last_name="o'neil", company=None,
                             job_title="Student", phone=None))                                   # cold
        self.ingest(raw_lead(email="broken@"))                                                  # rejected
        self.out = os.path.join(self.tmp, "out")
        self.counts = outputs.export_all(self.conn, self.out, self.cfg, generated_at="2026-10-01 00:00 UTC")

    def read_csv(self, name):
        with open(os.path.join(self.out, name), encoding="utf-8", newline="") as fh:
            return list(csv.reader(fh))

    def test_hubspot_header_exact(self):
        rows = self.read_csv("hubspot_contacts.csv")
        self.assertEqual(rows[0][:7], HUBSPOT_REQUIRED)
        self.assertEqual(rows[0], self.cfg["hubspot"]["columns"])
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(len(r) == len(rows[0]) for r in rows))

    def test_hubspot_values(self):
        data = self.read_csv("hubspot_contacts.csv")
        hot, cold = dict(zip(data[0], data[1])), dict(zip(data[0], data[2]))
        self.assertEqual(hot["Lead Status"], "Open")
        self.assertEqual(hot["Contact owner"], "amelia.hart@northbeam.example")   # owner by email, as HubSpot maps
        self.assertEqual(hot["Phone Number"], "+447700900123")
        self.assertEqual(hot["Country/Region"], "United Kingdom")
        self.assertEqual((cold["First Name"], cold["Last Name"], cold["Lead Status"], cold["Company Name"]),
                         ("Bob", "O'Neil", "New", ""))

    def test_hubspot_status_values_are_hubspot_defaults(self):
        allowed = {"New", "Open", "In progress", "Open deal", "Unqualified", "Attempted to contact", "Connected", "Bad timing"}
        self.assertTrue(set(self.cfg["hubspot"]["lead_status_by_tier"].values()) <= allowed)

    def test_slack_outbox_payload(self):
        with open(os.path.join(self.out, "slack_outbox.jsonl"), encoding="utf-8") as fh:
            lines = [json.loads(l) for l in fh if l.strip()]
        self.assertEqual(len(lines), 1)
        p = lines[0]
        self.assertTrue(p["text"].startswith("Hot lead: Ada Lovelace"))
        self.assertIn("<@U0SAMPLE01>", p["text"])
        self.assertEqual([b["type"] for b in p["blocks"]], ["header", "section", "context"])
        self.assertLessEqual(len(p["blocks"][1]["fields"]), 10)                    # Slack limit
        self.assertTrue(all(len(f["text"]) <= 2000 for f in p["blocks"][1]["fields"]))

    def test_export_is_deterministic_and_atomic(self):
        def digest():
            return {n: hashlib.sha256(read_bytes(os.path.join(self.out, n))).hexdigest()
                    for n in ("hubspot_contacts.csv", "slack_outbox.jsonl", "rejects.csv", "run_report.md")}
        first = digest()
        outputs.export_all(self.conn, self.out, self.cfg, generated_at="2026-10-01 00:00 UTC")
        self.assertEqual(first, digest())
        self.assertFalse([f for f in os.listdir(self.out) if f.endswith(".tmp")])

    def test_report_and_rejects(self):
        report = read_text(os.path.join(self.out, "run_report.md"))
        for heading in ("## Headline", "## Inbound by source", "## Rejected records", "## Routing", "## Hot leads"):
            self.assertIn(heading, report)
        self.assertIn("Sample project", report)
        rejects = self.read_csv("rejects.csv")
        self.assertEqual(rejects[0], ["source", "idempotency_key", "reason", "raw"])
        self.assertEqual(rejects[1][2], "invalid_email")


class EndToEndRecoveryTests(unittest.TestCase):
    """Generated 300-record data set: a crash + resume must yield byte-identical outputs to a clean run."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="leadflow-e2e-")
        self.cfg = config()
        self.counts = generator.generate(os.path.join(self.tmp, "data"))
        self.csv_path = os.path.join(self.tmp, "data", "tradeshow_leads.csv")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_pipeline(self, name, crash_after=None):
        conn = store.connect(os.path.join(self.tmp, name + ".sqlite3"))
        if crash_after is not None:
            with self.assertRaises(batch.SimulatedCrash):
                batch.run_batch(conn, self.csv_path, self.cfg, crash_after=crash_after)
            conn.close()
            conn = store.connect(os.path.join(self.tmp, name + ".sqlite3"))   # "restart the process"
        batch.run_batch(conn, self.csv_path, self.cfg)
        batch.run_batch(conn, self.csv_path, self.cfg)                         # and once more for good measure
        out = os.path.join(self.tmp, name + "-out")
        outputs.export_all(conn, out, self.cfg, generated_at="fixed")
        conn.close()
        return {n: read_bytes(os.path.join(out, n)) for n in ("hubspot_contacts.csv", "slack_outbox.jsonl", "rejects.csv")}

    def test_crash_recovery_produces_identical_outputs(self):
        clean = self.run_pipeline("clean")
        recovered = self.run_pipeline("crashy", crash_after=50)
        self.assertEqual(clean, recovered)

    def test_hard_kill_mid_transaction_then_resume(self):
        """Real process death (os._exit inside the open transaction) via the CLI, then re-run."""
        import subprocess
        import sys
        db = os.path.join(self.tmp, "killed.sqlite3")
        cmd = [sys.executable, "-m", "leadflow", "--db", db, "import-csv", self.csv_path]
        cwd = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        killed = subprocess.run(cmd + ["--crash-after", "30", "--crash-mode", "kill"], cwd=cwd, capture_output=True)
        self.assertEqual(killed.returncode, 137)
        conn = store.connect(db)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM ingest_log").fetchone()[0], 30)
        self.assertEqual(conn.execute("SELECT status FROM batch_runs").fetchone()[0], "running")  # died, no cleanup
        conn.close()
        resumed = subprocess.run(cmd, cwd=cwd, capture_output=True)
        self.assertEqual(resumed.returncode, 0, resumed.stderr)
        self.assertEqual(json.loads(resumed.stdout)["already_processed"], 30)
        conn = store.connect(db)
        self.assertEqual([r[0] for r in conn.execute("SELECT status FROM batch_runs ORDER BY run_id")],
                         ["interrupted", "completed"])
        conn.close()
        out = os.path.join(self.tmp, "killed-out")
        conn = store.connect(db)
        outputs.export_all(conn, out, self.cfg, generated_at="fixed")
        conn.close()
        clean = self.run_pipeline("clean2")
        self.assertEqual(clean["hubspot_contacts.csv"], read_bytes(os.path.join(out, "hubspot_contacts.csv")))

    def test_generator_volume_defects_and_determinism(self):
        self.assertGreaterEqual(self.counts["total_inbound_records"], 280)
        self.assertLessEqual(self.counts["total_inbound_records"], 320)
        self.assertGreater(self.counts["csv_exact_duplicates"], 0)
        self.assertGreater(self.counts["fuzzy_duplicates"], 0)
        again = os.path.join(self.tmp, "again")
        generator.generate(again)
        for name in ("webhook_events.jsonl", "tradeshow_leads.csv"):
            self.assertEqual(read_bytes(os.path.join(self.tmp, "data", name)), read_bytes(os.path.join(again, name)))
        self.assertIn(b"\x00", read_bytes(self.csv_path))


if __name__ == "__main__":
    unittest.main()
