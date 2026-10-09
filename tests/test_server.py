"""Webhook endpoint: signature, replay/idempotency, schema validation, HTTP error mapping."""
import json
import threading
import time
import unittest

from helpers import DBTestCase
from leadflow import demo, server

SECRET = "unit-test-secret"


def website_event(event_id="wev_test000001", **lead_kw):
    lead = {"first_name": "Grace", "last_name": "Hopper", "email": "grace.hopper@cobol-works.com",
            "phone": "(212) 555-0147", "company": "Cobol Works Inc", "job_title": "Director of Engineering",
            "country": "USA", "company_size": 300}
    lead.update(lead_kw)
    return {"event_id": event_id, "submitted_at": "2026-09-10T09:00:00Z", "form": "demo_request", "lead": lead}


def typeform_event(event_id="tf_test0000001", email="linus@kernel-labs.de"):
    def a(ref, kind, value):
        d = {"type": kind, "field": {"ref": ref}}
        d.update({"choice": {"label": value}} if kind == "choice" else {kind: value})
        return d
    return {"event_id": event_id, "event_type": "form_response", "form_response": {
        "form_id": "PRICECALC", "submitted_at": "2026-09-11T09:00:00Z", "answers": [
            a("first_name", "text", "Linus"), a("last_name", "text", "Berg"), a("email", "email", email),
            a("company_size", "number", 250), a("budget", "choice", "50k+"), a("timeline", "choice", "This quarter"),
            a("job_title", "text", "CTO"), a("country", "choice", "Germany"), a("phone", "phone_number", "+49 30 1234567")]}}


class WebhookServerTests(DBTestCase):
    def setUp(self):
        super().setUp()
        self.httpd, self.thread = server.serve_in_thread(self.cfg, self.db_path, SECRET)
        self.base = "http://127.0.0.1:%d" % self.httpd.server_address[1]

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        super().tearDown()

    def post(self, path, payload, **kw):
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        return demo.post(self.base + path, body, kw.pop("secret", SECRET), **kw)

    def leads(self):
        return self.count("SELECT COUNT(*) FROM leads")

    # -- signature ---------------------------------------------------------------------
    def test_valid_signature_creates_lead(self):
        status, body = self.post("/webhooks/website", website_event())
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "created")
        self.assertEqual(self.leads(), 1)

    def test_bad_signature_rejected_401_and_not_processed(self):
        status, body = self.post("/webhooks/website", website_event(), tamper=True)
        self.assertEqual(status, 401)
        self.assertIn("signature mismatch", body["error"])
        self.assertEqual(self.leads(), 0)

    def test_wrong_secret_rejected(self):
        status, _ = self.post("/webhooks/website", website_event(), secret="attacker-guess")
        self.assertEqual(status, 401)
        self.assertEqual(self.leads(), 0)

    def test_body_modified_after_signing_rejected(self):
        ts = int(time.time())
        signed = json.dumps(website_event()).encode()
        sig = server.sign(SECRET, ts, signed)
        tampered = signed.replace(b"Grace", b"Mallory")
        import urllib.request, urllib.error
        req = urllib.request.Request(self.base + "/webhooks/website", data=tampered, method="POST", headers={
            "Content-Type": "application/json", "X-Leadflow-Timestamp": str(ts), "X-Leadflow-Signature": sig})
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req)
        self.assertEqual(ctx.exception.code, 401)

    def test_missing_signature_header_rejected(self):
        import urllib.request, urllib.error
        req = urllib.request.Request(self.base + "/webhooks/website", data=json.dumps(website_event()).encode(),
                                     method="POST", headers={"Content-Type": "application/json"})
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req)
        self.assertEqual(ctx.exception.code, 401)
        self.assertIn("missing signature", json.loads(ctx.exception.read())["error"])

    def test_stale_timestamp_rejected(self):
        status, body = self.post("/webhooks/website", website_event(), timestamp=int(time.time()) - 3600)
        self.assertEqual(status, 401)
        self.assertIn("tolerance", body["error"])

    def test_verify_signature_unit(self):
        body = b'{"a":1}'
        sig = server.sign("s", 1000, body)
        self.assertIsNone(server.verify_signature("s", "1000", sig, body, 300, now=1100))
        self.assertEqual(server.verify_signature("s", "1000", sig, body + b" ", 300, now=1100), "signature mismatch")
        self.assertEqual(server.verify_signature("s", "abc", sig, body, 300, now=1100), "timestamp is not an integer")

    # -- idempotency -------------------------------------------------------------------
    def test_replayed_event_is_not_reprocessed(self):
        ev = website_event()
        first = self.post("/webhooks/website", ev)
        second = self.post("/webhooks/website", ev)
        self.assertEqual(first[1]["status"], "created")
        self.assertEqual(second, (200, {"status": "duplicate", "key": "website:wev_test000001",
                                        "event_id": "wev_test000001"}))
        self.assertEqual(self.leads(), 1)
        self.assertEqual(self.count("SELECT COUNT(*) FROM ingest_log"), 1)
        self.assertEqual(self.count("SELECT COUNT(*) FROM replay_log"), 1)

    def test_replay_does_not_duplicate_hot_lead_notification(self):
        ev = website_event()
        for _ in range(3):
            self.post("/webhooks/website", ev)
        self.assertEqual(self.count("SELECT COUNT(*) FROM notifications"), 1)

    def test_concurrent_replays_process_exactly_once(self):
        ev = website_event(event_id="wev_concurrent01")
        results = []

        def fire():
            results.append(self.post("/webhooks/website", ev))
        threads = [threading.Thread(target=fire) for _ in range(12)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        statuses = sorted(r[1]["status"] for r in results)
        self.assertEqual(statuses.count("created"), 1, statuses)
        self.assertEqual(statuses.count("duplicate"), 11, statuses)
        self.assertEqual(self.leads(), 1)

    def test_same_event_id_on_different_sources_is_distinct(self):
        self.post("/webhooks/website", website_event(event_id="shared_id_001"))
        status, body = self.post("/webhooks/typeform", typeform_event(event_id="shared_id_001"))
        self.assertEqual(body["status"], "created")

    # -- payload validation --------------------------------------------------------------
    def test_malformed_json_400(self):
        status, body = self.post("/webhooks/website", b'{"event_id": "wev_x", "lead": {')
        self.assertEqual(status, 400)
        self.assertIn("not valid JSON", body["error"])

    def test_schema_errors_422_with_paths(self):
        ev = website_event()
        del ev["lead"]["email"]
        ev["form"] = "webinar"
        ev["lead"]["company_size"] = "lots"
        ev["lead"]["fax"] = "123"
        status, body = self.post("/webhooks/website", ev)
        self.assertEqual(status, 422)
        paths = {d["path"]: d["message"] for d in body["details"]}
        self.assertEqual(paths["$.lead.email"], "is required")
        self.assertIn("must be one of", paths["$.form"])
        self.assertIn("expected integer or null", paths["$.lead.company_size"])
        self.assertEqual(paths["$.lead.fax"], "unexpected property")
        self.assertEqual(self.leads(), 0)

    def test_schema_error_event_can_be_resent_after_fix(self):
        bad = website_event(event_id="wev_fixme00001")
        bad["form"] = "webinar"
        self.assertEqual(self.post("/webhooks/website", bad)[0], 422)
        self.assertEqual(self.post("/webhooks/website", website_event(event_id="wev_fixme00001"))[1]["status"], "created")

    def test_typeform_empty_answers_422(self):
        ev = typeform_event()
        ev["form_response"]["answers"] = []
        status, body = self.post("/webhooks/typeform", ev)
        self.assertEqual(status, 422)
        self.assertEqual(body["details"][0]["path"], "$.form_response.answers")

    def test_typeform_happy_path_maps_answers(self):
        status, body = self.post("/webhooks/typeform", typeform_event())
        self.assertEqual((status, body["status"], body["tier"]), (200, "created", "hot"))
        row = self.conn.execute("SELECT data FROM leads").fetchone()
        lead = json.loads(row["data"])
        self.assertEqual((lead["country"], lead["phone"], lead["budget"], lead["timeline"]),
                         ("DE", "+49301234567", "50k+", "this quarter"))

    def test_invalid_email_is_accepted_but_quarantined(self):
        status, body = self.post("/webhooks/website", website_event(email="grace@@cobol-works.com"))
        self.assertEqual((status, body["status"], body["reason"]), (200, "rejected", "invalid_email"))
        self.assertEqual(self.leads(), 0)

    def test_wrong_content_type_415(self):
        status, _ = self.post("/webhooks/website", website_event(), content_type="text/plain")
        self.assertEqual(status, 415)

    def test_oversize_body_413(self):
        ev = website_event(message="x" * 70000)
        status, _ = self.post("/webhooks/website", ev)
        self.assertEqual(status, 413)

    def test_unknown_endpoint_404(self):
        status, _ = self.post("/webhooks/salesforce", website_event())
        self.assertEqual(status, 404)

    def test_rejections_are_audited(self):
        self.post("/webhooks/website", website_event(), tamper=True)
        self.post("/webhooks/website", b"not json")
        rows = self.conn.execute("SELECT status FROM http_rejections ORDER BY id").fetchall()
        self.assertEqual([r[0] for r in rows], [401, 400])

    def test_healthz(self):
        import urllib.request
        with urllib.request.urlopen(self.base + "/healthz") as resp:
            self.assertEqual(json.loads(resp.read()), {"status": "ok"})


if __name__ == "__main__":
    unittest.main()
