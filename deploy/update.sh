#!/usr/bin/env bash
# GitHub Actions から stdin で流して実行する。main の最新を取り込み再起動する。
# stock.db / .env は触らない。スキーマ不足列だけ migrate.py に任せる。
set -euo pipefail

APP_DIR="${APP_DIR:-/home/stock/stock-analyzer}"
cd "$APP_DIR"

if [ -n "$(git status --porcelain)" ]; then
  echo "VPS working tree is dirty:" >&2
  git status --porcelain
  exit 1
fi

exec 9>/tmp/stock-analyzer-deploy.lock
if ! flock -n 9; then
  echo "another deploy is running" >&2
  exit 1
fi

echo "==> $(date -Is) deploy start $(git rev-parse --short HEAD 2>/dev/null || echo none)"

git fetch origin main
git checkout -q main
git pull --ff-only origin main

if [ ! -x "$APP_DIR/.venv/bin/pip" ]; then
  echo "venv が無いので作ります"
  python3 -m venv "$APP_DIR/.venv"
fi
"$APP_DIR/.venv/bin/pip" install -r requirements.txt -q
"$APP_DIR/.venv/bin/python" scripts/migrate.py

sudo systemctl restart stock-analyzer

ok=0
for _ in $(seq 1 25); do
  if curl -fsS -o /dev/null http://127.0.0.1:8000/; then
    ok=1
    break
  fi
  sleep 1
done

if [ "$ok" -ne 1 ]; then
  echo "health check failed" >&2
  sudo systemctl --no-pager --full status stock-analyzer || true
  exit 1
fi

echo "health ok"
sudo systemctl is-active stock-analyzer
git log -1 --oneline
echo "==> $(date -Is) deploy done"
