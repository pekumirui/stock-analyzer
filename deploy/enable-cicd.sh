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
# clone 前は setup-github-ssh.sh がまだ無いので、鍵と GitHub host key だけ先に揃える
sudo -u "$STOCK_USER" env APP_DIR="$APP_DIR" REPO_SSH="$REPO_URL" bash -c '
  if [[ -f "$APP_DIR/deploy/setup-github-ssh.sh" ]]; then
    bash "$APP_DIR/deploy/setup-github-ssh.sh"
    exit 0
  fi
  KEY="$HOME/.ssh/id_github"
  if [[ ! -f "$KEY" ]]; then
    ssh-keygen -t ed25519 -C "stock-analyzer-vps-github" -f "$KEY" -N ""
  fi
  CFG="$HOME/.ssh/config"
  touch "$CFG"
  chmod 600 "$CFG"
  if ! grep -qE "^Host github\\.com$" "$CFG"; then
    printf "Host github.com\\n  IdentityFile %s\\n  IdentitiesOnly yes\\n" "$KEY" >> "$CFG"
  fi
  KNOWN="$HOME/.ssh/known_hosts"
  touch "$KNOWN"
  chmod 644 "$KNOWN"
  grep -qF "AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl" "$KNOWN" || \
    echo "github.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl" >> "$KNOWN"
'

if [[ ! -d "$APP_DIR/.git" ]]; then
  install -d -o "$STOCK_USER" -g "$STOCK_USER" "$(dirname "$APP_DIR")"
  if ! sudo -u "$STOCK_USER" git clone "$REPO_URL" "$APP_DIR"; then
    echo "clone 失敗。GitHub → Settings → Deploy keys に次の公開鍵を read-only で追加して再実行:" >&2
    cat "$STOCK_HOME/.ssh/id_github.pub" >&2
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
