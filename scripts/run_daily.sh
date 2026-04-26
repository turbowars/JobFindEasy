#!/usr/bin/env bash
# Daily pipeline. Cron-friendly: activates venv, runs full pipeline, logs.
#
# Add to crontab:
#   30 7 * * 1-5 cd /full/path/to/job-intelligence-agent && ./scripts/run_daily.sh

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
python -m src.cli run --score-limit 200 >> "$LOG" 2>&1
echo "===== run finished $(date -Iseconds) =====" >> "$LOG"

# Print last 20 lines for cron mail / immediate inspection
tail -n 20 "$LOG"
