#!/usr/bin/env bash
# エージェント起動時に :8000 で SPA を上げる。冪等。
# install で焼いた stock.db を使い、毎回 backfill はしない。
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

LOG=/tmp/stock-web.log
mkdir -p "$(dirname "$LOG")"
touch "$LOG"

if curl -sf -o /dev/null http://127.0.0.1:8000/; then
  echo "already up on :8000"
  exit 0
fi

nohup ./run.sh serve >>"$LOG" 2>&1 &

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
