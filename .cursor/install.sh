#!/usr/bin/env bash
# Cursor Cloud Agent の環境ビルド。冪等。
# apt → .venv → tools 依存 → JQUANTS_API_KEY があれば stock.db を 2 年分バックフィル。
# 2 年は MA200 + RS(252日) + 1か月傾きに要る約 14 か月を満たす最小の整数年。
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

sudo apt-get update -qq
sudo apt-get install -y -qq --no-install-recommends python3-venv poppler-utils rclone

./setup.sh
.venv/bin/python -m pip install -q -r tools/requirements.txt

if [[ -n "${JQUANTS_API_KEY:-}" ]]; then
  ./run.sh backfill 2
else
  echo "JQUANTS_API_KEY が無いので stock.db は作らない"
fi
