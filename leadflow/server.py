"""Webhook intake server (http.server, stdlib only).

    POST /webhooks/website    website contact / demo form
    POST /webhooks/typeform   Typeform form_response webhook
    GET  /healthz

Request checks, in order (cheapest and most security-relevant first):
  404 unknown path / 405 wrong method
  415 Content-Type is not application/json
  411/413 missing or oversize body (limit from config)
  401 missing/invalid signature or timestamp outside tolerance
      signature = "sha256=" + hex(HMAC_SHA256(secret, f"{timestamp}.".encode() + raw_body))
  400 body is not valid UTF-8 JSON
  422 JSON Schema violations (every error listed with its JSON path)
  200 {"status": "created" | "merged" | "rejected" | "duplicate", ...}
The signature is verified over the *raw bytes* before any parsing.
A replayed event id returns 200 "duplicate" so the sender stops retrying, but nothing is
re-processed (see pipeline.ingest).
"""
import hashlib
import hmac
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import pipeline, store
from .schema import load_schema, validate


def sign(secret, timestamp, body):
    mac = hmac.new(secret.encode("utf-8"), str(timestamp).encode("ascii") + b"." + body, hashlib.sha256)
    return "sha256=" + mac.hexdigest()


def verify_signature(secret, timestamp_header, signature_header, body, tolerance, now=None):
    """Return None if OK, else a short reason string."""
    if not signature_header:
        return "missing signature header"
    if not timestamp_header:
        return "missing timestamp header"
    try:
        ts = int(timestamp_header)
    except ValueError:
        return "timestamp is not an integer"
    now = int(time.time()) if now is None else now
    if abs(now - ts) > tolerance:
        return "timestamp outside tolerance window"
    expected = sign(secret, ts, body)
    if not hmac.compare_digest(expected.encode("ascii"), signature_header.strip().encode("ascii", "replace")):
        return "signature mismatch"
    return None


class WebhookHandler(BaseHTTPRequestHandler):
    server_version = "leadflow/1.0"
    # set by make_server
    config = None
    db_path = None
    secret = None
    quiet = True

    def log_message(self, fmt, *args):  # keep test output clean
        if not self.quiet:
            BaseHTTPRequestHandler.log_message(self, fmt, *args)

    def _send(self, status, body):
        data = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _reject(self, status, error, details=None):
        conn = store.connect(self.db_path)
        try:
            store.log_http_rejection(conn, self.path, status, error)
        finally:
            conn.close()
        body = {"error": error}
        if details is not None:
            body["details"] = details
        self._send(status, body)

    def do_GET(self):
        if self.path == "/healthz":
            return self._send(200, {"status": "ok"})
        return self._reject(404, "not found")

    def do_PUT(self):
        return self._reject(405, "method not allowed")

    do_DELETE = do_PATCH = do_PUT

    def do_POST(self):
        prefix = "/webhooks/"
        source_name = self.path[len(prefix):] if self.path.startswith(prefix) else None
        if source_name not in pipeline.ADAPTERS:
            return self._reject(404, "unknown webhook endpoint")
        wh = self.config["webhook"]

        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if ctype != "application/json":
            return self._reject(415, "Content-Type must be application/json")
        length_header = self.headers.get("Content-Length")
        if length_header is None:
            return self._reject(411, "Content-Length required")
        try:
            length = int(length_header)
        except ValueError:
            return self._reject(400, "invalid Content-Length")
        if length > wh["max_body_bytes"]:
            return self._reject(413, "body exceeds %d bytes" % wh["max_body_bytes"])
        body = self.rfile.read(length)

        problem = verify_signature(self.secret, self.headers.get(wh["timestamp_header"]),
                                   self.headers.get(wh["signature_header"]), body,
                                   wh["timestamp_tolerance_seconds"])
        if problem:
            return self._reject(401, "unauthorized: " + problem)

        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            return self._reject(400, "body is not valid JSON", str(exc))

        errors = validate(payload, load_schema(source_name))
        if errors:
            return self._reject(422, "payload failed schema validation", errors)

        source, adapter = pipeline.ADAPTERS[source_name]
        key = "%s:%s" % (source, payload["event_id"])
        conn = store.connect(self.db_path)
        try:
            result = pipeline.ingest(conn, adapter(payload), source, key, self.config)
            if result["status"] == "duplicate":
                conn.execute("INSERT INTO replay_log (key, source) VALUES (?, ?)", (key, source))
        except Exception as exc:  # pragma: no cover - surfaced as 500 so the sender retries
            return self._send(500, {"error": "internal error", "type": type(exc).__name__})
        finally:
            conn.close()
        result["event_id"] = payload["event_id"]
        return self._send(200, result)


def make_server(config, db_path, secret, host="127.0.0.1", port=0, quiet=True):
    if not secret:
        raise ValueError("webhook secret must be set (LEADFLOW_WEBHOOK_SECRET)")
    store.connect(db_path).close()  # create schema up-front
    handler = type("BoundWebhookHandler", (WebhookHandler,),
                   {"config": config, "db_path": db_path, "secret": secret, "quiet": quiet})
    return ThreadingHTTPServer((host, port), handler)


def serve_in_thread(config, db_path, secret, port=0):
    httpd = make_server(config, db_path, secret, port=port)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, thread
