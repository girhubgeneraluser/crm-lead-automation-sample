#!/bin/sh
# Failure-injection demo: crash the trade-show import mid-transaction, then recover by re-running.
# Sample project - synthetic data, not client work.
set -u
cd "$(dirname "$0")/.."
rm -f var/clean.sqlite3* var/crash.sqlite3*
rm -rf out/failure-demo && mkdir -p out/failure-demo

echo "== 0. reference run: webhooks + clean, uninterrupted CSV import into its own database"
python3 -m leadflow --db var/clean.sqlite3 deliver-webhooks data/webhook_events.jsonl > /dev/null
python3 -m leadflow --db var/clean.sqlite3 import-csv data/tradeshow_leads.csv > /dev/null
python3 -m leadflow --db var/clean.sqlite3 --out out/failure-demo/clean export > /dev/null

echo "== 1. same webhooks, then a CSV import that is hard-killed (os._exit(137), no cleanup) mid-transaction on row 51"
python3 -m leadflow --db var/crash.sqlite3 deliver-webhooks data/webhook_events.jsonl > /dev/null
python3 -m leadflow --db var/crash.sqlite3 status | grep -E '"(leads|ledger_entries|hot_notifications)"'
python3 -m leadflow --db var/crash.sqlite3 import-csv data/tradeshow_leads.csv --crash-after 50
echo "exit code: $?"

echo "== 2. state after the crash: 50 rows committed, row 51 never committed, batch run left in 'running'"
python3 -m leadflow --db var/crash.sqlite3 status

echo "== 3. recovery = run the exact same command again"
python3 -m leadflow --db var/crash.sqlite3 import-csv data/tradeshow_leads.csv
echo "exit code: $?"

echo "== 4. and once more (must be a no-op)"
python3 -m leadflow --db var/crash.sqlite3 import-csv data/tradeshow_leads.csv | grep -E '"(created|merged|rejected|already_processed)"'

echo "== 4b. batch run history"
python3 -m leadflow --db var/crash.sqlite3 status | grep -E '"(run_id|status)"' | paste - -

echo "== 5. compare exports: recovered vs clean"
python3 -m leadflow --db var/crash.sqlite3 --out out/failure-demo/recovered export > /dev/null
for f in hubspot_contacts.csv slack_outbox.jsonl rejects.csv; do
  printf '%4s lines  ' "$(wc -l < "out/failure-demo/recovered/$f" | tr -d ' ')"
  if cmp -s "out/failure-demo/clean/$f" "out/failure-demo/recovered/$f"; then
    echo "IDENTICAL  $f  $(shasum -a 256 "out/failure-demo/recovered/$f" | cut -c1-16)"
  else
    echo "DIFFERENT  $f"; exit 1
  fi
done
