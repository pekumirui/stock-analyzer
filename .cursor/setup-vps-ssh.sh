#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
KNOWN_HOSTS="${ROOT}/deploy/known_hosts"
HOST="${VPS_HOST:-160.251.179.50}"
USER="${VPS_USER:-stock}"
PORT="${VPS_PORT:-22}"

RO_KEY="${HOME}/.ssh/id_vps_ro"

if [[ -z "${VPS_SSH_KEY:-}" && -z "${VPS_RO_KEY:-}" && ! -f "$RO_KEY" ]]; then
  echo "VPS_SSH_KEY も VPS_RO_KEY も無いので VPS SSH はスキップ"
  exit 0
fi

mkdir -p "${HOME}/.ssh"
chmod 700 "${HOME}/.ssh"

write_key() {
  KEY_ENV="$1" KEY_PATH="$2" python3 - <<'PY'
import os
from pathlib import Path
raw = os.environ[os.environ["KEY_ENV"]].replace("\r\n", "\n").replace("\r", "\n").strip() + "\n"
key = Path(os.environ["KEY_PATH"])
key.write_text(raw)
key.chmod(0o600)
PY
}

[[ -n "${VPS_SSH_KEY:-}" ]] && write_key VPS_SSH_KEY "${HOME}/.ssh/id_vps"
[[ -n "${VPS_RO_KEY:-}" ]] && write_key VPS_RO_KEY "$RO_KEY"

if [[ -f "$KNOWN_HOSTS" ]]; then
  grep -v '^#' "$KNOWN_HOSTS" | grep -v '^$' > "${HOME}/.ssh/known_hosts.vps" || true
  if [[ -s "${HOME}/.ssh/known_hosts.vps" ]]; then
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

add_host() {
  local alias="$1" key="$2"
  if grep -qE "^Host[[:space:]]+${alias}[[:space:]]*$" "$CFG" 2>/dev/null; then
    return 0
  fi
  cat >> "$CFG" <<EOF

Host ${alias}
  HostName ${HOST}
  User ${USER}
  Port ${PORT}
  IdentityFile ~/.ssh/${key}
  IdentitiesOnly yes
  BatchMode yes
  StrictHostKeyChecking yes
  UserKnownHostsFile ~/.ssh/known_hosts
  ConnectTimeout 15
EOF
}

if [[ -f "${HOME}/.ssh/id_vps" ]]; then
  add_host stock-vps id_vps
  echo "VPS SSH ready: ssh stock-vps"
  if ssh -o BatchMode=yes stock-vps 'echo ok $(hostname)' 2>/dev/null; then
    echo "VPS SSH check: ok"
  else
    echo "VPS SSH check: failed (鍵が stock の authorized_keys にあるか確認)"
  fi
fi

if [[ -f "$RO_KEY" ]]; then
  add_host stock-vps-ro id_vps_ro
  echo "本番の読み取り: ssh stock-vps-ro（slice / get だけ）"
fi
