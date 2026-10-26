#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/home/stock/stock-analyzer}"
cd "$APP_DIR"

tracked="$(git status --porcelain --untracked-files=no)"
if [ -n "$tracked" ]; then
  echo "Deploy must go git → CI → VPS only." >&2
  echo "Hand edits / SCP changes on the VPS are not allowed and are detected to stop deploy." >&2
  printf '%s\n' "$tracked" >&2
  exit 1
fi

LOCK_FILE="${DEPLOY_LOCK:-/tmp/stock-analyzer-deploy.lock}"
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  echo "another deploy is running" >&2
  exit 1
fi

echo "==> $(date -Is) deploy start $(git rev-parse --short HEAD 2>/dev/null || echo none)"

# https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/githubs-ssh-key-fingerprints
# ssh.github.com は github.com と同じ鍵。port 443 は [host]:443 形式が必要。
github_keys() {
  local key
  local keys
  keys="$(cat <<'EOF'
ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl
ecdsa-sha2-nistp256 AAAAE2VjZHNhLXNoYTItbmlzdHAyNTYAAAAIbmlzdHAyNTYAAABBBEmKSENjQEezOmxkZMy7opKgwFB9nkt5YRrYMjNuG5N87uRgg6CLrbo5wAdT/y6v0mKV0U2w0WZ2YB/++Tpockg=
ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQCj7ndNxQowgcQnjshcLrqPEiiphnt+VTTvDP6mHBL9j1aNUkY4Ue1gvwnGLVlOhGeYrnZaMgRK6+PKCUXaDbC7qtbW8gIkhL7aGCsOr/C56SJMy/BCZfxd1nWzAOxSDPgVsmerOBYfNqltV9/hWCqBywINIR+5dIg6JTJ72pcEpEjcYgXkE2YEFXV1JHnsKgbLWNlhScqb2UmyRkQyytRLtL+38TGxkxCflmO+5Z8CSSNY7GidjMIZ7Q4zMjA2n1nGrlTDkzwDCsw+wqFPGQA179cnfGWOWRVruj16z6XyvxvjJwbz0wQZ75XK5tKSb7FNyeIEs4TT4jk+S4dhPeAUC5y+bDYirYgM4GC7uEnztnZyaVWQ7B381AK4Qdrwt51ZqExKbQpTUNn+EjqoTwvqNj4kqx5QUCI0ThS/YkOxJCXmPUWZbhjpCg56i+2aB6CmK2JGhn57K5mj0MNdBXA4/WnwH6XoPWJzK5Nyu2zB3nAZp+S5hpQs+p1vN1/wsjk=
EOF
)"
  local host
  for host in github.com ssh.github.com "[ssh.github.com]:443"; do
    while IFS= read -r key; do
      [[ -z "$key" ]] && continue
      printf '%s %s\n' "$host" "$key"
    done <<< "$keys"
  done
}

github_ssh_bootstrap() {
  install -d -m 700 "$HOME/.ssh"
  local key=""
  if [[ -f "$HOME/.ssh/id_github" ]]; then
    key="$HOME/.ssh/id_github"
  elif [[ -f "$HOME/.ssh/id_ed25519" ]]; then
    key="$HOME/.ssh/id_ed25519"
  else
    key="$HOME/.ssh/id_github"
    ssh-keygen -t ed25519 -C "stock-analyzer-vps-github" -f "$key" -N ""
    echo "generated $key"
  fi
  chmod 600 "$key"
  chmod 644 "${key}.pub"
  echo "key $key"

  local cfg="$HOME/.ssh/config"
  touch "$cfg"
  chmod 600 "$cfg"
  local tmp
  tmp="$(mktemp)"
  awk '
    BEGIN { skip=0 }
    /^Host[[:space:]]+github\.com[[:space:]]*$/ { skip=1; next }
    /^Host[[:space:]]/ || /^Match[[:space:]]/ { skip=0 }
    skip { next }
    { print }
  ' "$cfg" > "$tmp"
  {
    echo "Host github.com"
    echo "  HostName ssh.github.com"
    echo "  Port 443"
    echo "  HostKeyAlias github.com"
    echo "  User git"
    echo "  IdentityFile $key"
    echo "  IdentitiesOnly yes"
  } >> "$tmp"
  mv "$tmp" "$cfg"
  chmod 600 "$cfg"
  echo "wrote $cfg"

  local known="$HOME/.ssh/known_hosts"
  touch "$known"
  chmod 644 "$known"
  local line
  while IFS= read -r line; do
    [[ -z "$line" ]] && continue
    if ! grep -qxF "$line" "$known"; then
      printf '%s\n' "$line" >> "$known"
    fi
  done < <(github_keys)

  if [[ -n "${ADMIN_SSH_PUBKEY:-}" ]]; then
    local auth="$HOME/.ssh/authorized_keys"
    touch "$auth"
    chmod 600 "$auth"
    if ! grep -qxF "$ADMIN_SSH_PUBKEY" "$auth"; then
      printf '%s\n' "$ADMIN_SSH_PUBKEY" >> "$auth"
      echo "added ADMIN_SSH_PUBKEY to authorized_keys"
    fi
  fi

  local repo_ssh="${REPO_SSH:-git@github.com:pekumirui/stock-analyzer.git}"
  if [[ -d "$APP_DIR/.git" ]]; then
    local origin
    origin="$(git -C "$APP_DIR" remote get-url origin 2>/dev/null || true)"
    case "$origin" in
      git@github.com:*|https://github.com/*|http://github.com/*|ssh://git@github.com/*)
        git -C "$APP_DIR" remote set-url origin "$repo_ssh"
        echo "origin $(git -C "$APP_DIR" remote get-url origin)"
        ;;
    esac
  fi

  echo "DEPLOY_PUBKEY_BEGIN"
  cat "${key}.pub"
  echo "DEPLOY_PUBKEY_END"
}

github_ssh_bootstrap

if ! git fetch origin main; then
  echo "git fetch 失敗。プライベートリポジトリは Deploy keys (read-only) が必要です。" >&2
  echo "hint: GitHub SSH uses ssh.github.com port 443 (outbound 22 often times out)." >&2
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
upsert_env ESTAT_APP_ID "${ESTAT_APP_ID:-}"
upsert_env EDINET_API_KEY "${EDINET_API_KEY:-}"
upsert_env MCP_TOKEN "${MCP_TOKEN:-}"
upsert_env R2_ACCESS_KEY_ID "${R2_ACCESS_KEY_ID:-}"
upsert_env R2_SECRET_ACCESS_KEY "${R2_SECRET_ACCESS_KEY:-}"

if ! grep -qE '^MCP_TOKEN=.+' "$APP_DIR/.env" 2>/dev/null; then
  upsert_env MCP_TOKEN "$(openssl rand -hex 32)"
  echo "generated MCP_TOKEN on VPS (value not printed; set GitHub secret MCP_TOKEN to pin it)"
fi

write_rclone_conf() {
  if [ -z "${R2_ACCESS_KEY_ID:-}" ] || [ -z "${R2_SECRET_ACCESS_KEY:-}" ]; then
    return 0
  fi
  # [r2] だけ upsert。onedrive など他 remote は消さない（deploy/write_rclone_conf.py）。
  python3 "$APP_DIR/deploy/write_rclone_conf.py"
}

umask 077
write_rclone_conf

chmod +x "$APP_DIR/deploy/provision.sh"
# --preserve-env names not yet in live env_keep are rejected before provision
# can install sudoers. Refresh /etc/sudoers.d with APP_DIR only (kept since
# enable-cicd), then pass channel webhooks on the full run.
sudo --preserve-env=APP_DIR \
  "$APP_DIR/deploy/provision.sh" --sudoers-only
sudo --preserve-env=APP_DIR,VPS_DOMAIN,BASIC_AUTH_USER,BASIC_AUTH_PASSWORD,CERTBOT_EMAIL,SLACK_WEBHOOK_URL,SLACK_WEBHOOK_URL_APP,SLACK_WEBHOOK_URL_BATCH,SLACK_WEBHOOK_URL_INFRA,SLACK_WEBHOOK_URL_QUIET \
  "$APP_DIR/deploy/provision.sh"

install_venv() {
  echo "venv が無いので作ります"
  local staging
  staging="$(mktemp -d "$APP_DIR/.venv.building.XXXXXX")"
  python3 -m venv "$staging"
  if [ -e "$APP_DIR/.venv" ] || [ -L "$APP_DIR/.venv" ]; then
    rm -rf "$APP_DIR/.venv.old"
    mv "$APP_DIR/.venv" "$APP_DIR/.venv.old"
  fi
  if ! mv "$staging" "$APP_DIR/.venv"; then
    if [ -e "$APP_DIR/.venv.old" ] || [ -L "$APP_DIR/.venv.old" ]; then
      mv "$APP_DIR/.venv.old" "$APP_DIR/.venv"
    fi
    rm -rf "$staging"
    return 1
  fi
  rm -rf "$APP_DIR/.venv.old"
}

ensure_app_venv() {
  local venv_bin="$APP_DIR/.venv/bin"
  venv_py=""
  if [ -x "$venv_bin/python3" ]; then
    venv_py="$venv_bin/python3"
  elif [ -x "$venv_bin/python" ]; then
    venv_py="$venv_bin/python"
  fi
  if [ -L "$APP_DIR/.venv" ] || [ -z "$venv_py" ] || ! "$venv_py" -c "" 2>/dev/null; then
    install_venv
    venv_bin="$APP_DIR/.venv/bin"
    if [ -x "$venv_bin/python3" ]; then
      venv_py="$venv_bin/python3"
    else
      venv_py="$venv_bin/python"
    fi
  fi
  if [ -x "$venv_bin/python3" ] && [ ! -x "$venv_bin/python" ]; then
    ln -sfn python3 "$venv_bin/python"
  fi
  if ! "$venv_py" -m pip --version >/dev/null 2>&1; then
    echo "venv に pip が無いので入れ直します"
    "$venv_py" -m ensurepip --upgrade
  fi
}

venv_py=""
ensure_app_venv
"$venv_py" -m pip install -r requirements.txt -q
"$venv_py" jobs/migrate.py

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
