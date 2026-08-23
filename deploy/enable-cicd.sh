#!/usr/bin/env bash
# 一度だけ root で実行する。あとは GitHub Actions が provision.sh で初期設定する。
#
# 手元:
#   ssh-keygen -t ed25519 -C "github-actions@stock-analyzer" -f stock-analyzer-deploy -N ""
#   # 秘密鍵 → GitHub Actions secret VPS_SSH_KEY
#   ssh -t root@YOUR_HOST 'bash -s' < deploy/enable-cicd.sh   # 公開鍵は引数でも可
#   ssh -t root@YOUR_HOST "bash /home/stock/stock-analyzer/deploy/enable-cicd.sh '$(cat stock-analyzer-deploy.pub)'"
set -euo pipefail

STOCK_USER="${STOCK_USER:-stock}"
REPO_URL="${REPO_URL:-https://github.com/pekumirui/stock-analyzer.git}"
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

if [[ ! -d "$APP_DIR/.git" ]]; then
  install -d -o "$STOCK_USER" -g "$STOCK_USER" "$(dirname "$APP_DIR")"
  sudo -u "$STOCK_USER" git clone "$REPO_URL" "$APP_DIR"
  echo "clone $APP_DIR"
else
  echo "repo は既にある ($APP_DIR)"
fi

AUTH="$STOCK_HOME/.ssh/authorized_keys"
install -d -m 700 -o "$STOCK_USER" -g "$STOCK_USER" "$STOCK_HOME/.ssh"
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

chmod +x "$APP_DIR/deploy/provision.sh" "$APP_DIR/deploy/update.sh"

echo "完了。GitHub の VPS_SSH_KEY を入れて Actions を回せば nginx / ufw / certbot / systemd まで入ります。"
