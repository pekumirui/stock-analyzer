#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

sudo apt-get update -qq
sudo apt-get install -y -qq --no-install-recommends python3-venv poppler-utils rclone

# PATH で /usr/bin より先に立つラッパー。remote r2 を R2_* から定義する（鍵はディスクに書かない）。
# AWS_CA_BUNDLE（プロキシ環境）があると S3 が LoadCustomCABundleError で落ちるので --ca-cert で渡し直す
sudo tee /usr/local/bin/rclone >/dev/null <<'SH'
#!/bin/sh
if [ -n "${AWS_CA_BUNDLE:-}" ]; then
  set -- --ca-cert "$AWS_CA_BUNDLE" "$@"
  unset AWS_CA_BUNDLE
fi
if [ -n "${R2_ACCESS_KEY_ID:-}" ] && [ -n "${R2_SECRET_ACCESS_KEY:-}" ]; then
  export RCLONE_CONFIG_R2_TYPE=s3
  export RCLONE_CONFIG_R2_PROVIDER=Cloudflare
  export RCLONE_CONFIG_R2_ENDPOINT=https://39a6da16651b53d44c459c3a4f4d59f5.r2.cloudflarestorage.com
  export RCLONE_CONFIG_R2_ACCESS_KEY_ID="$R2_ACCESS_KEY_ID"
  export RCLONE_CONFIG_R2_SECRET_ACCESS_KEY="$R2_SECRET_ACCESS_KEY"
fi
exec /usr/bin/rclone "$@"
SH
sudo chmod 755 /usr/local/bin/rclone

./setup.sh
.venv/bin/python -m pip install -q -r tools/requirements.txt

bash .cursor/setup-vps-ssh.sh || echo "setup-vps-ssh skipped"

# autoMemoryDirectory はコミットする .claude/settings.json では無視されるので settings.local.json に書く
python3 - <<'PY'
import json, pathlib
p = pathlib.Path(".claude/settings.local.json")
s = json.loads(p.read_text()) if p.exists() else {}
s["autoMemoryDirectory"] = str(pathlib.Path(".claude/memory").resolve())
p.write_text(json.dumps(s, ensure_ascii=False, indent=2) + "\n")
PY

is_golden_db() {
  [[ "$(.venv/bin/python -c 'import sqlite3; print(sqlite3.connect("stock.db").execute("PRAGMA user_version").fetchone()[0])')" == "1" ]]
}

if [[ ! -f stock.db ]]; then
  if [[ "${STOCK_DB:-golden}" == "full" ]]; then
    if [[ -z "${JQUANTS_API_KEY:-}" ]]; then
      echo "STOCK_DB=full には JQUANTS_API_KEY が要る" >&2
      exit 1
    fi
    echo "STOCK_DB=full なので backfill 2"
    ./run.sh backfill 2
  else
    echo "stock.db が無いのでゴールデンデータで作る（STOCK_DB=full で backfill）"
    ./run.sh golden
  fi
elif is_golden_db; then
  echo "ゴールデンデータの DB なので日次 batch は回さない"
elif [[ -n "${JQUANTS_API_KEY:-}" ]]; then
  echo "stock.db があるので日次 batch だけ回す"
  ./run.sh batch || echo "batch skipped"
else
  echo "JQUANTS_API_KEY が無いので stock.db は触らない"
fi

if [[ -f stock.db ]]; then
  bash .cursor/sync-monthly-from-vps.sh
fi
