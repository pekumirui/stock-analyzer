#!/usr/bin/env bash
# Cursor Cloud Agent から VPS へ SSH するための鍵配置。冪等。
# Secrets に VPS_SSH_KEY（GitHub Actions と同じデプロイ秘密鍵）があれば有効。
# 値を echo しない。鍵ファイルは 600。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
KNOWN_HOSTS="${ROOT}/deploy/known_hosts"
HOST="${VPS_HOST:-160.251.179.50}"
USER="${VPS_USER:-stock}"
PORT="${VPS_PORT:-22}"

if [[ -z "${VPS_SSH_KEY:-}" ]]; then
  echo "VPS_SSH_KEY が無いので VPS SSH はスキップ"
  exit 0
fi

mkdir -p "${HOME}/.ssh"
chmod 700 "${HOME}/.ssh"

python3 - <<'PY'
import os
from pathlib import Path
raw = os.environ["VPS_SSH_KEY"].replace("\r\n", "\n").replace("\r", "\n").strip() + "\n"
key = Path.home() / ".ssh" / "id_vps"
key.write_text(raw)
key.chmod(0o600)
PY

# known_hosts はリポジトリのピン留めを使う（TOFU しない）。冪等に差し替える。
if [[ -f "$KNOWN_HOSTS" ]]; then
  grep -v '^#' "$KNOWN_HOSTS" | grep -v '^$' > "${HOME}/.ssh/known_hosts.vps" || true
  if [[ -s "${HOME}/.ssh/known_hosts.vps" ]]; then
    # 他ホスト行は残し、この HOST の行だけ差し替え
    if [[ -f "${HOME}/.ssh/known_hosts" ]]; then
      grep -v "^${HOST} " "${HOME}/.ssh/known_hosts" > "${HOME}/.ssh/known_hosts.tmp" || true
      mv "${HOME}/.ssh/known_hosts.tmp" "${HOME}/.ssh/known_hosts"
    else
      : > "${HOME}/.ssh/known_hosts"
    fi
    cat "${HOME}/.ssh/known_hosts.vps" >> "${HOME}/.ssh/known_hosts"
    chmod 600 "${HOME}/.ssh/known_hosts"
  fi
fi

CFG="${HOME}/.ssh/config"
touch "$CFG"
chmod 600 "$CFG"
if ! grep -qE '^Host[[:space:]]+stock-vps[[:space:]]*$' "$CFG" 2>/dev/null; then
  cat >> "$CFG" <<EOF

Host stock-vps
  HostName ${HOST}
  User ${USER}
  Port ${PORT}
  IdentityFile ~/.ssh/id_vps
  IdentitiesOnly yes
  BatchMode yes
  StrictHostKeyChecking yes
  UserKnownHostsFile ~/.ssh/known_hosts
  ConnectTimeout 15
EOF
fi

echo "VPS SSH ready: ssh stock-vps"
# 接続確認（失敗しても install は落とさない。鍵未登録・一時障害があり得る）
if ssh -o BatchMode=yes stock-vps 'echo ok $(hostname)' 2>/dev/null; then
  echo "VPS SSH check: ok"
else
  echo "VPS SSH check: failed (鍵が stock の authorized_keys にあるか確認)"
fi
