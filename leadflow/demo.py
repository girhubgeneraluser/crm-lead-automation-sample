"""End-to-end demo: generate data -> real HTTP webhook server -> signed deliveries -> CSV batch
-> exports. Everything runs locally; the only "network" is 127.0.0.1."""
import json
import os
import time
import urllib.error
import urllib.request

from . import batch, generator, outputs, server, store

DEMO_SECRET = "demo-secret-not-for-production"


def post(url, body_bytes, secret, timestamp=None, tamper=False, content_type="application/json"):
    ts = int(time.time()) if timestamp is None else timestamp
    sig = server.sign(secret, ts, body_bytes)
    if tamper:
        sig = sig[:-4] + ("0000" if not sig.endswith("0000") else "1111")
    req = urllib.request.Request(url, data=body_bytes, method="POST", headers={
        "Content-Type": content_type, "X-Leadflow-Timestamp": str(ts), "X-Leadflow-Signature": sig})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read().decode("utf-8") or "{}")


def deliver_webhooks(base_url, events_path, secret):
    tally = {}
    with open(events_path, encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            ev = json.loads(line)
            body = ev["body"].encode("utf-8")
            url = "%s/webhooks/%s" % (base_url, ev["endpoint"])
            if ev["scenario"] == "bad_signature":
                status, resp = post(url, body, secret, tamper=True)
            elif ev["scenario"] == "stale_timestamp":
                status, resp = post(url, body, secret, timestamp=int(time.time()) - 3600)
            else:
                status, resp = post(url, body, secret)
            label = "%d %s" % (status, resp.get("status") or resp.get("error", ""))
            tally[label] = tally.get(label, 0) + 1
    return tally


def run_demo(root, config, fresh=True, verbose=True):
    data_dir, out_dir = os.path.join(root, "data"), os.path.join(root, "out")
    db_path = os.path.join(root, "var", "leadflow.sqlite3")
    if fresh:
        for suffix in ("", "-wal", "-shm"):
            if os.path.exists(db_path + suffix):
                os.remove(db_path + suffix)
    log = print if verbose else (lambda *a, **k: None)

    counts = generator.generate(data_dir)
    log("[1/4] generated synthetic data: %s" % json.dumps(counts, sort_keys=True))

    httpd, _thread = server.serve_in_thread(config, db_path, DEMO_SECRET)
    base = "http://127.0.0.1:%d" % httpd.server_address[1]
    try:
        tally = deliver_webhooks(base, os.path.join(data_dir, "webhook_events.jsonl"), DEMO_SECRET)
    finally:
        httpd.shutdown()
        httpd.server_close()
    log("[2/4] webhook deliveries via %s -> %s" % (base, json.dumps(tally, sort_keys=True)))

    conn = store.connect(db_path)
    stats = batch.run_batch(conn, os.path.join(data_dir, "tradeshow_leads.csv"), config)
    log("[3/4] trade-show CSV batch -> %s" % json.dumps(stats, sort_keys=True))

    exported = outputs.export_all(conn, out_dir, config)
    conn.close()
    log("[4/4] exports written to %s -> %s" % (out_dir, json.dumps(exported, sort_keys=True)))
    return {"generated": counts, "webhooks": tally, "batch": stats, "exported": exported}
