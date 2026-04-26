#!/usr/bin/env bash
# Daily morning pipeline. Cron-friendly: activates venv, runs the *low-cost*
# steps only, logs.
#
# Why no `scrape` here:
#   The autoscrape thread inside Streamlit (and the runner's per-source
#   `skip_if_scraped_within_minutes` window) already keeps the DB fresh on
#   its own cadence (default 6 hours). This cron's job is to run the
#   non-scrape pieces — score any unscored survivors and fire the morning
#   notification — without burning duplicate API calls.
#
# Add to crontab:
#   0 8 * * * /full/path/to/JobFindEasy/scripts/run_daily.sh

set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$(pwd)"

# Activate venv if present
if [ -f ".venv/bin/activate" ]; then
    # shellcheck disable=SC1091
    source .venv/bin/activate
fi

# Ensure data dir exists
mkdir -p data/exports

LOG="data/run-$(date +%F).log"

echo "===== run started $(date -Iseconds) =====" >> "$LOG"
# Prefilter any rows that haven't been filtered yet (safe; scrape did the
# inserts, autoscrape may have only filtered some), then score, then notify.
python -m src.cli prefilter      >> "$LOG" 2>&1 || true
python -m src.cli score --limit 200 >> "$LOG" 2>&1 || true
python -m src.cli notify         >> "$LOG" 2>&1 || true
echo "===== run finished $(date -Iseconds) =====" >> "$LOG"

# Print last 20 lines for cron mail / immediate inspection
tail -n 20 "$LOG"
