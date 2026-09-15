#!/usr/bin/env bash
# Cursor Cloud Agent の環境ビルド。冪等。
# apt → .venv → tools 依存 → JQUANTS_API_KEY があれば stock.db を用意。
# 初回だけ backfill 2（MA200 + RS 252日 + 1か月傾きに要る約 14 か月を満たす）。
# 既に stock.db がある Recurring Build は日次 batch だけ（毎回 2 年は取らない）。
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

sudo apt-get update -qq
sudo apt-get install -y -qq --no-install-recommends python3-venv poppler-utils rclone

./setup.sh
.venv/bin/python -m pip install -q -r tools/requirements.txt

# 任意: Cursor Secrets の VPS_SSH_KEY があれば stock-vps へ SSH 可能にする
bash .cursor/setup-vps-ssh.sh || echo "setup-vps-ssh skipped"

if [[ -z "${JQUANTS_API_KEY:-}" ]]; then
  echo "JQUANTS_API_KEY が無いので stock.db は触らない"
  exit 0
fi

if [[ -f stock.db ]]; then
  echo "stock.db があるので日次 batch だけ回す"
  ./run.sh batch || echo "batch skipped"
else
  echo "stock.db が無いので初回 backfill 2"
  ./run.sh backfill 2
fi
