#!/usr/bin/env bash
# GitHub Actions から stdin で流して実行する。
# git pull → 冪等な初期設定 (provision.sh) → venv → migrate → 再起動
# stock.db は触らない。.env は JQUANTS_API_KEY / MCP_TOKEN が渡されたときだけ当該行を更新する。
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

upsert_env() {
  local key="$1" val="${2:-}"
  local file="$APP_DIR/.env"
  [ -n "$val" ] || return 0
  umask 077
  touch "$file"
  chmod 600 "$file"
  local tmp
  tmp="$(mktemp)"
  if [ -s "$file" ]; then
    grep -v "^${key}=" "$file" > "$tmp" || true
  fi
  printf '%s=%s\n' "$key" "$val" >> "$tmp"
  mv "$tmp" "$file"
  chmod 600 "$file"
  echo "updated .env ${key}"
}

upsert_env JQUANTS_API_KEY "${JQUANTS_API_KEY:-}"
upsert_env MCP_TOKEN "${MCP_TOKEN:-}"

if ! grep -qE '^MCP_TOKEN=.+' "$APP_DIR/.env" 2>/dev/null; then
  upsert_env MCP_TOKEN "$(openssl rand -hex 32)"
  echo "generated MCP_TOKEN on VPS (value not printed; set GitHub secret MCP_TOKEN to pin it)"
fi

chmod +x "$APP_DIR/deploy/provision.sh"
sudo --preserve-env=APP_DIR,VPS_DOMAIN,BASIC_AUTH_USER,BASIC_AUTH_PASSWORD,CERTBOT_EMAIL \
  "$APP_DIR/deploy/provision.sh"

if [ ! -x "$APP_DIR/.venv/bin/pip" ]; then
  echo "venv が無いので作ります"
  python3 -m venv "$APP_DIR/.venv"
fi
"$APP_DIR/.venv/bin/pip" install -r requirements.txt -q
"$APP_DIR/.venv/bin/python" scripts/migrate.py

sudo systemctl restart stock-analyzer
sudo systemctl restart stock-analyzer-mcp

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

mcp_ok=0
mcp_code=""
for _ in $(seq 1 15); do
  mcp_code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 2 http://127.0.0.1:8010/mcp || true)"
  if [ "$mcp_code" = "401" ] || [ "$mcp_code" = "200" ]; then
    mcp_ok=1
    break
  fi
  sleep 1
done

if [ "$mcp_ok" -ne 1 ]; then
  echo "mcp health check failed (http=$mcp_code)" >&2
  sudo systemctl --no-pager --full status stock-analyzer-mcp || true
  exit 1
fi

oauth_code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 2 http://127.0.0.1:8010/.well-known/oauth-authorization-server || true)"
if [ "$oauth_code" != "200" ]; then
  echo "oauth metadata health check failed (http=$oauth_code)" >&2
  sudo systemctl --no-pager --full status stock-analyzer-mcp || true
  exit 1
fi

echo "health ok"
echo "mcp health ok ($mcp_code)"
echo "oauth metadata ok ($oauth_code)"
sudo systemctl is-active stock-analyzer
sudo systemctl is-active stock-analyzer-mcp
git log -1 --oneline
echo "==> $(date -Is) deploy done"
