"""Duplicate detection across sources and the documented merge rules."""
import json
import unittest

from helpers import DBTestCase, raw_lead
from leadflow import store


class DedupeTests(DBTestCase):
    def lead(self, lead_id):
        return store.load_lead(self.conn, lead_id)

    def test_same_email_across_three_sources_is_one_contact(self):
        a = self.ingest(raw_lead(), source="website")
        b = self.ingest(raw_lead(email="  ADA.LOVELACE@Analytical-Engines.co.uk "), source="tradeshow")
        c = self.ingest(raw_lead(email="Ada.Lovelace@analytical-engines.co.uk"), source="typeform")
        self.assertEqual([a["status"], b["status"], c["status"]], ["created", "merged", "merged"])
        self.assertEqual(b["match_type"], "email")
        self.assertEqual(self.count("SELECT COUNT(*) FROM leads"), 1)
        lead = self.lead(a["lead_id"])
        self.assertEqual(lead["sources"], ["tradeshow", "typeform", "website"])
        self.assertEqual(lead["touch_count"], 3)
        self.assertEqual(lead["original_source"], "website")

    def test_fuzzy_freemail_plus_typo_merges_and_work_email_becomes_primary(self):
        first = self.ingest(raw_lead(email="ada.lovelace77@gmail.com", last_name="Lovelase", company="ANALYTICAL ENGINES LIMITED"),
                            source="tradeshow")
        second = self.ingest(raw_lead(company=None), source="website")  # company inferred from domain
        self.assertEqual((second["status"], second["match_type"]), ("merged", "fuzzy"))
        lead = self.lead(first["lead_id"])
        self.assertEqual(lead["email"], "ada.lovelace@analytical-engines.co.uk")
        self.assertEqual(lead["emails"], ["ada.lovelace77@gmail.com", "ada.lovelace@analytical-engines.co.uk"])
        self.assertEqual(lead["last_name"], "Lovelase")  # first-touch name kept
        # later arrival via the *old* personal address still matches exactly
        third = self.ingest(raw_lead(email="ADA.LOVELACE77@GMAIL.COM", company=None), source="typeform")
        self.assertEqual((third["status"], third["match_type"], third["lead_id"]), ("merged", "email", first["lead_id"]))

    def test_different_people_same_company_not_merged(self):
        self.ingest(raw_lead(first_name="Mark", last_name="Jones", email="mark.jones@acme.com", company="Acme"))
        r = self.ingest(raw_lead(first_name="Mike", last_name="Jones", email="mike.jones@acme.com", company="Acme"))
        self.assertEqual(r["status"], "created")

    def test_same_name_different_company_not_merged(self):
        self.ingest(raw_lead(email="ada@acme.com", company="Acme"))
        r = self.ingest(raw_lead(email="ada@globex.com", company="Globex"))
        self.assertEqual(r["status"], "created")

    def test_two_distinct_corporate_mailboxes_need_similar_local_parts(self):
        self.ingest(raw_lead(email="ada.lovelace@analytical-engines.co.uk"))
        r = self.ingest(raw_lead(email="research-desk@analytical-engines.co.uk"))
        self.assertEqual(r["status"], "created")  # same name, but a shared mailbox is not the same person

    def test_merge_rules_fill_blanks_latest_wins_and_explicit_company(self):
        a = self.ingest(raw_lead(job_title="Analyst", phone="12", company=None, submitted_at="2026-09-01T00:00:00Z"))
        self.ingest(raw_lead(job_title="Head of Research", phone="07700 900999", company="Analytical Engines Ltd",
                             budget="50k+", submitted_at="2026-09-05T00:00:00Z"), source="tradeshow")
        lead = self.lead(a["lead_id"])
        self.assertEqual(lead["job_title"], "Head of Research")       # most recent wins
        self.assertEqual(lead["phone"], "+447700900999")              # valid beats unparseable
        self.assertEqual(lead["company"], "Analytical Engines Ltd")   # explicit beats inferred
        self.assertFalse(lead["company_inferred"])
        self.assertEqual(lead["budget"], "50k+")                      # blank filled
        # an *older* record arriving later does not overwrite newer values
        self.ingest(raw_lead(job_title="Intern", submitted_at="2026-08-01T00:00:00Z"), source="typeform")
        lead = self.lead(a["lead_id"])
        self.assertEqual(lead["job_title"], "Head of Research")
        self.assertEqual((lead["first_seen"], lead["last_seen"]), ("2026-08-01T00:00:00Z", "2026-09-05T00:00:00Z"))

    def test_owner_sticky_and_tier_never_downgrades_on_merge(self):
        a = self.ingest(raw_lead(job_title="CEO", company_size=500, form="demo_request"))
        self.assertEqual(a["tier"], "hot")
        b = self.ingest(raw_lead(job_title="Student", company_size=3,
                                 submitted_at="2026-09-09T00:00:00Z"), source="tradeshow")
        self.assertEqual(b["owner"], a["owner"])
        self.assertEqual(b["tier"], "hot")
        self.assertLess(b["score"], a["score"] + 10)
        self.assertEqual(self.count("SELECT COUNT(*) FROM notifications"), 1)

    def test_rejected_record_does_not_create_lead(self):
        r = self.ingest(raw_lead(email="ada@@acme.com"))
        self.assertEqual(r["status"], "rejected")
        row = self.conn.execute("SELECT outcome, reason, raw FROM ingest_log").fetchone()
        self.assertEqual((row["outcome"], row["reason"]), ("rejected", "invalid_email"))
        self.assertEqual(json.loads(row["raw"])["email"], "ada@@acme.com")


if __name__ == "__main__":
    unittest.main()
