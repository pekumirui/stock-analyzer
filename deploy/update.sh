#!/usr/bin/env bash
# GitHub Actions から stdin で流して実行する。
# git pull → 冪等な初期設定 (provision.sh) → venv → migrate → 再起動
# stock.db は触らない。.env は JQUANTS_API_KEY / MCP_TOKEN / R2_* が渡されたときだけ当該行を更新する。
# SLACK_WEBHOOK_URL は .env に書かない。provision が /etc/fluent-bit/slack.env へ書く。
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

if [[ -f "$APP_DIR/deploy/setup-github-ssh.sh" ]]; then
  bash "$APP_DIR/deploy/setup-github-ssh.sh"
fi

if ! git fetch origin main; then
  echo "git fetch 失敗。プライベートリポジトリは Deploy keys (read-only) が必要です。" >&2
  pub=""
  if [[ -f "$HOME/.ssh/id_github.pub" ]]; then
    pub="$HOME/.ssh/id_github.pub"
  elif [[ -f "$HOME/.ssh/id_ed25519.pub" ]]; then
    pub="$HOME/.ssh/id_ed25519.pub"
  fi
  if [[ -n "$pub" ]]; then
    echo "DEPLOY_PUBKEY_BEGIN" >&2
    cat "$pub" >&2
    echo "DEPLOY_PUBKEY_END" >&2
  fi
  exit 1
fi
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
upsert_env R2_ACCESS_KEY_ID "${R2_ACCESS_KEY_ID:-}"
upsert_env R2_SECRET_ACCESS_KEY "${R2_SECRET_ACCESS_KEY:-}"

if ! grep -qE '^MCP_TOKEN=.+' "$APP_DIR/.env" 2>/dev/null; then
  upsert_env MCP_TOKEN "$(openssl rand -hex 32)"
  echo "generated MCP_TOKEN on VPS (value not printed; set GitHub secret MCP_TOKEN to pin it)"
fi

write_rclone_conf() {
  # 値はファイルにだけ書く。stdout に出さない。
  if [ -z "${R2_ACCESS_KEY_ID:-}" ] || [ -z "${R2_SECRET_ACCESS_KEY:-}" ]; then
    return 0
  fi
  python3 - <<'PY'
import os
from pathlib import Path

key = os.environ.get("R2_ACCESS_KEY_ID") or ""
secret = os.environ.get("R2_SECRET_ACCESS_KEY") or ""
if not key or not secret:
    raise SystemExit(0)
conf_dir = Path.home() / ".config" / "rclone"
conf_dir.mkdir(parents=True, exist_ok=True)
conf_dir.chmod(0o700)
text = (
    "[r2]\n"
    "type = s3\n"
    "provider = Cloudflare\n"
    f"access_key_id = {key}\n"
    f"secret_access_key = {secret}\n"
    "region = auto\n"
    "endpoint = https://39a6da16651b53d44c459c3a4f4d59f5.r2.cloudflarestorage.com\n"
    "acl = private\n"
    "force_path_style = true\n"
    "v2_auth = false\n"
)
path = conf_dir / "rclone.conf"
path.write_text(text, encoding="utf-8")
path.chmod(0o600)
print("updated rclone.conf (keys not printed)")
PY
}

umask 077
write_rclone_conf

chmod +x "$APP_DIR/deploy/provision.sh"
sudo --preserve-env=APP_DIR,VPS_DOMAIN,BASIC_AUTH_USER,BASIC_AUTH_PASSWORD,CERTBOT_EMAIL,SLACK_WEBHOOK_URL \
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
