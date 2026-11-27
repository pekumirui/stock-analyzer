#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

LOG=/tmp/stock-web.log
mkdir -p "$(dirname "$LOG")"
touch "$LOG"

if curl -sf -o /dev/null http://127.0.0.1:8000/; then
  echo "already up on :8000"
  exit 0
fi

if [[ "${WEB_API:-}" == "prod" ]]; then
  nohup ./run.sh serve-prod >>"$LOG" 2>&1 &
else
  nohup ./run.sh serve >>"$LOG" 2>&1 &
fi

for _ in $(seq 1 60); do
  if curl -sf -o /dev/null http://127.0.0.1:8000/; then
    echo "serve ready on :8000"
    exit 0
  fi
  sleep 1
done

echo "serve failed to become ready"
tail -n 80 "$LOG" || true
exit 1
