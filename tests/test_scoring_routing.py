"""Scoring thresholds (config-driven) and routing fairness / territory / stickiness."""
import collections
import unittest

from helpers import DBTestCase, config, raw_lead
from leadflow import pipeline, scoring, store


class ScoringTests(unittest.TestCase):
    def setUp(self):
        self.cfg = {"thresholds": {"hot": 70, "warm": 40},
                    "rules": [{"id": "a", "field": "x", "op": "gte", "value": 0, "points": 0}]}

    def score(self, points):
        cfg = dict(self.cfg, rules=[{"id": "p", "field": "x", "op": "eq", "value": 1, "points": points}])
        return scoring.score_lead({"x": 1}, cfg)

    def test_threshold_boundaries(self):
        self.assertEqual(self.score(70)[1], "hot")
        self.assertEqual(self.score(69)[1], "warm")
        self.assertEqual(self.score(40)[1], "warm")
        self.assertEqual(self.score(39)[1], "cold")

    def test_clamped_0_to_100(self):
        self.assertEqual(self.score(250)[0], 100)
        self.assertEqual(self.score(-30)[0], 0)

    def test_ops(self):
        lead = {"t": "VP of Sales", "n": 120, "s": "50k+", "e": None}
        rules = [{"id": "c", "field": "t", "op": "contains_any", "value": ["vp"], "points": 1},
                 {"id": "b", "field": "n", "op": "between", "value": [50, 199], "points": 2},
                 {"id": "i", "field": "s", "op": "in", "value": ["50k+"], "points": 4},
                 {"id": "p", "field": "e", "op": "present", "value": False, "points": 8},
                 {"id": "g", "field": "t", "op": "gte", "value": 3, "points": 100}]  # non-numeric -> no match
        score, _, reasons = scoring.score_lead(lead, {"thresholds": {"hot": 70, "warm": 40}, "rules": rules})
        self.assertEqual((score, reasons), (15, ["c(+1)", "b(+2)", "i(+4)", "p(+8)"]))

    def test_default_config_examples(self):
        cfg = pipeline.load_config()["scoring"]
        hot = {"email_type": "corporate", "job_title": "Director of Ops", "company_size": 250, "budget": "50k+",
               "timeline": "this quarter", "phone_valid": True, "touch_count": 1}
        self.assertEqual(scoring.score_lead(hot, cfg)[:2], (95, "hot"))
        cold = {"email_type": "freemail", "job_title": "Student", "touch_count": 1}
        self.assertEqual(scoring.score_lead(cold, cfg)[:2], (0, "cold"))

    def test_config_change_changes_outcome_without_code(self):
        cfg = config(scoring__thresholds={"hot": 30, "warm": 10})["scoring"]
        self.assertEqual(scoring.score_lead({"email_type": "corporate", "job_title": "CEO"}, cfg)[1], "hot")

    def test_invalid_config_detected(self):
        bad = {"thresholds": {"hot": 10, "warm": 40}, "rules": [{"id": "x", "field": "f", "op": "regex", "points": 1}]}
        problems = scoring.validate_config(bad)
        self.assertEqual(len(problems), 2)


class RoutingTests(DBTestCase):
    def uk_lead(self, i, **kw):
        # distinct companies so account stickiness does not apply
        return raw_lead(email="person%d@company%d.co.uk" % (i, i), company="Company %d" % i,
                        first_name="P%d" % i, last_name="Q%d" % i, country="UK", **kw)

    def test_round_robin_fairness_within_territory(self):
        owners = [self.ingest(self.uk_lead(i))["owner"] for i in range(31)]
        counts = collections.Counter(owners)
        self.assertEqual(set(counts), {"amelia.hart@northbeam.example", "owen.price@northbeam.example"})
        self.assertLessEqual(max(counts.values()) - min(counts.values()), 1)
        self.assertEqual(owners[:4], ["amelia.hart@northbeam.example", "owen.price@northbeam.example"] * 2)

    def test_fairness_three_rep_territory_and_persistence_across_restart(self):
        owners = []
        for i in range(10):
            owners.append(self.ingest(raw_lead(email="a%d@firm%d.com" % (i, i), company="Firm %d" % i,
                                               first_name="A%d" % i, last_name="B%d" % i, country="USA"))["owner"])
        self.conn.close()                                    # simulate process restart
        self.conn = store.connect(self.db_path)
        for i in range(10, 30):
            owners.append(self.ingest(raw_lead(email="a%d@firm%d.com" % (i, i), company="Firm %d" % i,
                                               first_name="A%d" % i, last_name="B%d" % i, country="USA"))["owner"])
        self.assertEqual(sorted(collections.Counter(owners).values()), [10, 10, 10])

    def test_inactive_rep_skipped(self):
        cfg = config()
        cfg["routing"]["reps"]["owen.price@northbeam.example"]["active"] = False
        owners = {self.ingest(self.uk_lead(i), cfg=cfg)["owner"] for i in range(5)}
        self.assertEqual(owners, {"amelia.hart@northbeam.example"})

    def test_territories_and_default(self):
        cases = {"Germany": "DACH", "Switzerland": "DACH", "Canada": "North America", "Ireland": "UK & Ireland",
                 "Singapore": "Rest of World", None: "Rest of World"}
        for i, (country, terr) in enumerate(cases.items()):
            r = self.ingest(raw_lead(email="t%d@biz%d.com" % (i, i), company="Biz %d" % i, first_name="T%d" % i,
                                     country=country, phone=None))
            self.assertEqual(store.load_lead(self.conn, r["lead_id"])["territory"], terr, country)

    def test_account_stickiness(self):
        first = self.ingest(raw_lead(email="ceo@kestrel-labs.co.uk", first_name="Kim", last_name="Ng", company="Kestrel Labs"))
        others = [self.ingest(raw_lead(email="p%d@kestrel-labs.co.uk" % i, first_name="Z%d" % i, last_name="Y%d" % i,
                                       company=None))["owner"] for i in range(4)]
        self.assertEqual(set(others), {first["owner"]})
        lead = store.load_lead(self.conn, 2)
        self.assertEqual(lead["routing_method"], "account")

    def test_stickiness_can_be_disabled(self):
        cfg = config(routing__account_stickiness=False)
        owners = {self.ingest(raw_lead(email="p%d@kestrel-labs.co.uk" % i, first_name="Z%d" % i, last_name="Y%d" % i),
                              cfg=cfg)["owner"] for i in range(4)}
        self.assertEqual(len(owners), 2)


if __name__ == "__main__":
    unittest.main()
