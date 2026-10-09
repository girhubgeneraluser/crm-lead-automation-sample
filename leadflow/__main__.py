"""CLI. Run from the project root:  python3 -m leadflow <command> [options]

  generate                 write synthetic inputs to data/
  serve                    run the webhook server (needs LEADFLOW_WEBHOOK_SECRET)
  deliver-webhooks FILE    start the server in-process and POST every delivery in a webhook_events.jsonl
  import-csv FILE          import a trade-show CSV (resumable; --crash-after N injects a crash)
  export                   write out/hubspot_contacts.csv, slack_outbox.jsonl, rejects.csv, run_report.md
  status                   counts of leads / ledger entries / batch runs in the database
  demo                     full end-to-end run on fresh state
"""
import argparse
import json
import os
import sys

from . import batch, demo, generator, outputs, pipeline, server, store

ROOT = pipeline.ROOT


def main(argv=None):
    ap = argparse.ArgumentParser(prog="leadflow", description="Unified lead intake (sample project, synthetic data).")
    ap.add_argument("--config", default=pipeline.DEFAULT_CONFIG)
    ap.add_argument("--db", default=os.path.join(ROOT, "var", "leadflow.sqlite3"))
    ap.add_argument("--out", default=os.path.join(ROOT, "out"))
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generate")
    g.add_argument("--seed", type=int, default=20260917)
    s = sub.add_parser("serve")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8787)
    d = sub.add_parser("deliver-webhooks")
    d.add_argument("file")
    i = sub.add_parser("import-csv")
    i.add_argument("file")
    i.add_argument("--crash-after", type=int, default=None, help="fault injection: crash while committing row N+1")
    i.add_argument("--crash-mode", choices=["exception", "kill"], default="kill",
                   help="kill = os._exit(137) mid-transaction (default); exception = raise and roll back")
    sub.add_parser("export")
    sub.add_parser("status")
    sub.add_parser("demo")
    args = ap.parse_args(argv)

    try:
        config = pipeline.load_config(args.config)
    except pipeline.ConfigError as exc:
        print("config error: %s" % exc, file=sys.stderr)
        return 2

    if args.cmd == "generate":
        print(json.dumps(generator.generate(os.path.join(ROOT, "data"), seed=args.seed), indent=2, sort_keys=True))
    elif args.cmd == "serve":
        secret = os.environ.get("LEADFLOW_WEBHOOK_SECRET")
        if not secret:
            print("set LEADFLOW_WEBHOOK_SECRET first", file=sys.stderr)
            return 2
        httpd = server.make_server(config, args.db, secret, host=args.host, port=args.port, quiet=False)
        print("listening on http://%s:%d  (POST /webhooks/website, /webhooks/typeform)" % httpd.server_address)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
    elif args.cmd == "deliver-webhooks":
        secret = os.environ.get("LEADFLOW_WEBHOOK_SECRET", demo.DEMO_SECRET)
        httpd, _ = server.serve_in_thread(config, args.db, secret)
        try:
            tally = demo.deliver_webhooks("http://127.0.0.1:%d" % httpd.server_address[1], args.file, secret)
        finally:
            httpd.shutdown()
            httpd.server_close()
        print(json.dumps(tally, indent=2, sort_keys=True))
    elif args.cmd == "import-csv":
        conn = store.connect(args.db)
        try:
            stats = batch.run_batch(conn, args.file, config, crash_after=args.crash_after, crash_mode=args.crash_mode)
        except batch.SimulatedCrash as exc:
            print("CRASH: %s - transaction rolled back; re-run the same command to resume." % exc, file=sys.stderr)
            return 3
        except batch.BatchError as exc:
            print("batch error: %s" % exc, file=sys.stderr)
            return 2
        print(json.dumps(stats, indent=2, sort_keys=True))
    elif args.cmd == "export":
        conn = store.connect(args.db)
        print(json.dumps(outputs.export_all(conn, args.out, config), indent=2, sort_keys=True))
    elif args.cmd == "status":
        conn = store.connect(args.db)
        q = lambda sql: conn.execute(sql).fetchone()[0]
        print(json.dumps({
            "leads": q("SELECT COUNT(*) FROM leads"),
            "ledger_entries": q("SELECT COUNT(*) FROM ingest_log"),
            "hot_notifications": q("SELECT COUNT(*) FROM notifications"),
            "batch_runs": [dict(r) for r in conn.execute(
                "SELECT run_id, file_name, status, rows_total FROM batch_runs ORDER BY run_id")],
        }, indent=2))
    elif args.cmd == "demo":
        demo.run_demo(ROOT, config)
    return 0


if __name__ == "__main__":
    sys.exit(main())
