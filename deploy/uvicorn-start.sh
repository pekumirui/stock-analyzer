#!/usr/bin/env bash
# uvicorn のワーカー数は deploy/uvicorn_workers.py（nproc と MemAvailable）。
# UVICORN_WORKERS で上書き。--preload は使わない（fork 前の SQLite 接続がワーカーで壊れる）。
# lru_cache（app/api/monthly/_common.py, app/supply/chain.py）はワーカーごとに持つ。
set -euo pipefail
APP_DIR="${APP_DIR:-/home/stock/stock-analyzer}"
cd "$APP_DIR"
if [[ -x "$APP_DIR/.venv/bin/python3" ]]; then
  PY="$APP_DIR/.venv/bin/python3"
elif [[ -x "$APP_DIR/.venv/bin/python" ]]; then
  PY="$APP_DIR/.venv/bin/python"
else
  echo "venv python missing" >&2
  exit 1
fi
WORKERS="$("$PY" "$APP_DIR/deploy/uvicorn_workers.py")"
echo "uvicorn workers=${WORKERS}"
if [[ -x "$APP_DIR/.venv/bin/uvicorn" ]]; then
  exec "$APP_DIR/.venv/bin/uvicorn" app.main:app --host 127.0.0.1 --port 8000 \
    --workers "$WORKERS" --log-config deploy/uvicorn-log.json
fi
exec "$PY" -m uvicorn app.main:app --host 127.0.0.1 --port 8000 \
  --workers "$WORKERS" --log-config deploy/uvicorn-log.json
