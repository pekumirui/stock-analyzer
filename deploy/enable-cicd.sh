#!/usr/bin/env bash
# 一度だけ VPS で実行する。GitHub Actions から SSH デプロイできるようにする。
#
# 手元:
#   ssh-keygen -t ed25519 -C "github-actions@stock-analyzer" -f stock-analyzer-deploy -N ""
#   # stock-analyzer-deploy      → GitHub → Settings → Secrets and variables → Actions
#   #                              Name: VPS_SSH_KEY  (秘密鍵の中身をそのまま)
#   ssh -t root@YOUR_HOST "cd /home/stock/stock-analyzer && sudo bash deploy/enable-cicd.sh '$(cat stock-analyzer-deploy.pub)'"
set -euo pipefail

STOCK_USER="${STOCK_USER:-stock}"
STOCK_HOME="$(getent passwd "$STOCK_USER" | cut -d: -f6 || true)"
APP_DIR="${APP_DIR:-${STOCK_HOME:-/home/stock}/stock-analyzer}"

if [[ ${EUID} -ne 0 ]]; then
  echo "root で実行してください: sudo bash deploy/enable-cicd.sh '<public key>'" >&2
  exit 1
fi
if [[ -z "${STOCK_HOME}" ]]; then
  echo "ユーザ ${STOCK_USER} が見つかりません" >&2
  exit 1
fi

PUBKEY="${1:-}"
if [[ -z "$PUBKEY" ]]; then
  echo "デプロイ用の公開鍵を第1引数で渡してください" >&2
  exit 1
fi
PUBKEY="$(printf '%s' "$PUBKEY" | tr -d '\r' | head -n1)"
if [[ ! "$PUBKEY" =~ ^(ssh-(ed25519|rsa)|ecdsa-sha2-nistp256) ]]; then
  echo "公開鍵の形式が不正です" >&2
  exit 1
fi

AUTH="$STOCK_HOME/.ssh/authorized_keys"
install -d -m 700 -o "$STOCK_USER" -g "$STOCK_USER" "$STOCK_HOME/.ssh"
touch "$AUTH"
chown "$STOCK_USER:$STOCK_USER" "$AUTH"
chmod 600 "$AUTH"
if grep -qxF "$PUBKEY" "$AUTH"; then
  echo "authorized_keys に既に同じ鍵があります"
else
  printf '%s\n' "$PUBKEY" >> "$AUTH"
  echo "authorized_keys に追加しました"
fi

SRC="$APP_DIR/deploy/sudoers"
if [[ ! -f "$SRC" ]]; then
  echo "$SRC がありません。先にリポジトリを clone / pull してください" >&2
  exit 1
fi
install -m 440 "$SRC" /etc/sudoers.d/stock-analyzer
visudo -cf /etc/sudoers.d/stock-analyzer >/dev/null
echo "sudoers を入れました (/etc/sudoers.d/stock-analyzer)"

echo "完了。GitHub の Actions secret VPS_SSH_KEY に秘密鍵を入れて main をマージするとデプロイされます。"
