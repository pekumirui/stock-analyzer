#!/usr/bin/env bash
set -euo pipefail

STOCK_USER="${STOCK_USER:-stock}"
REPO_URL="${REPO_URL:-git@github.com:pekumirui/stock-analyzer.git}"
export DEBIAN_FRONTEND=noninteractive

if [[ ${EUID} -ne 0 ]]; then
  echo "root で実行してください: sudo bash deploy/enable-cicd.sh '<public key>'" >&2
  exit 1
fi

PUBKEY="${1:-}"
if [[ -z "$PUBKEY" ]]; then
  echo "公開鍵なし。authorized_keys が既にあれば sudoers だけ更新します"
fi
if [[ -n "$PUBKEY" ]]; then
  PUBKEY="$(printf '%s' "$PUBKEY" | tr -d '\r' | head -n1)"
  if [[ ! "$PUBKEY" =~ ^(ssh-(ed25519|rsa)|ecdsa-sha2-nistp256) ]]; then
    echo "公開鍵の形式が不正です" >&2
    exit 1
  fi
fi

apt-get update -qq
apt-get install -y -qq git sudo

if ! id -u "$STOCK_USER" >/dev/null 2>&1; then
  adduser --disabled-password --gecos "" "$STOCK_USER"
  echo "ユーザ ${STOCK_USER} を作成しました"
fi

STOCK_HOME="$(getent passwd "$STOCK_USER" | cut -d: -f6)"
APP_DIR="${APP_DIR:-${STOCK_HOME}/stock-analyzer}"

install -d -m 700 -o "$STOCK_USER" -g "$STOCK_USER" "$STOCK_HOME/.ssh"
sudo -u "$STOCK_USER" env APP_DIR="$APP_DIR" REPO_SSH="$REPO_URL" bash -s <<'GITHUB_SSH_BOOTSTRAP'
set -euo pipefail
install -d -m 700 "$HOME/.ssh"
if [[ -f "$HOME/.ssh/id_github" ]]; then
  KEY="$HOME/.ssh/id_github"
elif [[ -f "$HOME/.ssh/id_ed25519" ]]; then
  KEY="$HOME/.ssh/id_ed25519"
else
  KEY="$HOME/.ssh/id_github"
  ssh-keygen -t ed25519 -C "stock-analyzer-vps-github" -f "$KEY" -N ""
fi
chmod 600 "$KEY"
chmod 644 "${KEY}.pub"
CFG="$HOME/.ssh/config"
touch "$CFG"
chmod 600 "$CFG"
tmp="$(mktemp)"
awk '
  BEGIN { skip=0 }
  /^Host[[:space:]]+github\.com[[:space:]]*$/ { skip=1; next }
  /^Host[[:space:]]/ || /^Match[[:space:]]/ { skip=0 }
  skip { next }
  { print }
' "$CFG" > "$tmp"
{
  echo "Host github.com"
  echo "  HostName ssh.github.com"
  echo "  Port 443"
  echo "  HostKeyAlias github.com"
  echo "  User git"
  echo "  IdentityFile $KEY"
  echo "  IdentitiesOnly yes"
} >> "$tmp"
mv "$tmp" "$CFG"
chmod 600 "$CFG"
KNOWN="$HOME/.ssh/known_hosts"
touch "$KNOWN"
chmod 644 "$KNOWN"
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
while IFS= read -r line; do
  [[ -z "$line" ]] && continue
  if ! grep -qxF "$line" "$KNOWN"; then
    printf '%s\n' "$line" >> "$KNOWN"
  fi
done < <(github_keys)
GITHUB_SSH_BOOTSTRAP

if [[ ! -d "$APP_DIR/.git" ]]; then
  install -d -o "$STOCK_USER" -g "$STOCK_USER" "$(dirname "$APP_DIR")"
  if ! sudo -u "$STOCK_USER" git clone "$REPO_URL" "$APP_DIR"; then
    echo "clone 失敗。GitHub → Settings → Deploy keys に次の公開鍵を read-only で追加して再実行:" >&2
    pub=""
    if [[ -f "$STOCK_HOME/.ssh/id_github.pub" ]]; then
      pub="$STOCK_HOME/.ssh/id_github.pub"
    elif [[ -f "$STOCK_HOME/.ssh/id_ed25519.pub" ]]; then
      pub="$STOCK_HOME/.ssh/id_ed25519.pub"
    fi
    if [[ -n "$pub" ]]; then
      cat "$pub" >&2
    fi
    exit 1
  fi
  echo "clone $APP_DIR"
else
  echo "repo は既にある ($APP_DIR)"
  if [[ -f "$APP_DIR/deploy/setup-github-ssh.sh" ]]; then
    sudo -u "$STOCK_USER" env APP_DIR="$APP_DIR" REPO_SSH="$REPO_URL" \
      bash "$APP_DIR/deploy/setup-github-ssh.sh"
  fi
fi

AUTH="$STOCK_HOME/.ssh/authorized_keys"
touch "$AUTH"
chown "$STOCK_USER:$STOCK_USER" "$AUTH"
chmod 600 "$AUTH"
if [[ -n "$PUBKEY" ]]; then
  if grep -qxF "$PUBKEY" "$AUTH"; then
    echo "authorized_keys に既に同じ鍵があります"
  else
    printf '%s\n' "$PUBKEY" >> "$AUTH"
    echo "authorized_keys に追加しました"
  fi
elif ! grep -qE '^(ssh-(ed25519|rsa)|ecdsa-sha2-nistp256)' "$AUTH"; then
  echo "authorized_keys が空です。公開鍵を第1引数で渡してください" >&2
  exit 1
fi

SRC="$APP_DIR/deploy/sudoers"
if [[ ! -f "$SRC" ]]; then
  echo "$SRC がありません" >&2
  exit 1
fi
install -m 440 "$SRC" /etc/sudoers.d/stock-analyzer
visudo -cf /etc/sudoers.d/stock-analyzer >/dev/null
echo "sudoers を入れました (/etc/sudoers.d/stock-analyzer)"

if [[ -f "$APP_DIR/deploy/admin_keys" ]]; then
  install -d -m 700 /root/.ssh
  touch /root/.ssh/authorized_keys
  chmod 600 /root/.ssh/authorized_keys
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ -z "${line// }" || "$line" == \#* ]] && continue
    if ! grep -qxF "$line" /root/.ssh/authorized_keys; then
      printf '%s\n' "$line" >> /root/.ssh/authorized_keys
      echo "root authorized_keys に管理用公開鍵を追加しました"
    fi
    if ! grep -qxF "$line" "$AUTH"; then
      printf '%s\n' "$line" >> "$AUTH"
      echo "stock authorized_keys に管理用公開鍵を追加しました"
    fi
  done < "$APP_DIR/deploy/admin_keys"
fi

chmod +x "$APP_DIR/deploy/provision.sh" "$APP_DIR/deploy/update.sh" "$APP_DIR/deploy/setup-github-ssh.sh"

echo "完了。GitHub の VPS_SSH_KEY を入れて Actions を回せば nginx / ufw / certbot / systemd まで入ります。"
