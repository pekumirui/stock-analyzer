#!/usr/bin/env bash
# Linux セットアップ
# 使い方:  ./setup.sh
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3.10+ が見つかりません。python3 をインストールしてください。"
  exit 1
fi
ver_out="$(python3 --version 2>&1)"
if [[ ! "$ver_out" =~ Python\ ([0-9]+)\.([0-9]+) ]]; then
  echo "Python 3.10+ が見つかりません。python3 をインストールしてください。"
  exit 1
fi
maj="${BASH_REMATCH[1]}"
min="${BASH_REMATCH[2]}"
if (( maj < 3 || (maj == 3 && min < 10) )); then
  echo "Python 3.10+ が見つかりません。python3 をインストールしてください。"
  exit 1
fi
echo "使用する Python: python3 ($ver_out)"

if [[ ! -e .venv ]]; then
  echo "仮想環境を作成中 (.venv)..."
  python3 -m venv .venv
fi
.venv/bin/python -m pip install --upgrade pip
echo "依存パッケージをインストール中..."
.venv/bin/python -m pip install -r requirements.txt

if [[ -f stock.db && ! -s stock.db ]]; then
  rm -f stock.db stock.db-journal
fi

echo ""
echo "セットアップ完了。次:"
echo "  cp .env.example .env   # JQUANTS_API_KEY を記入"
echo "  ./run.sh backfill            # 初回データ取得"
echo "  ./run.sh serve               # サーバー起動 → http://localhost:8000"
