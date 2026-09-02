#!/usr/bin/env bash
# 実行ヘルパー
#   ./run.sh serve          開発サーバー起動 (http://localhost:8000)
#   ./run.sh serve 8080     ポート指定
#   ./run.sh check          J-Quants疎通・項目マッピング確認 (要 .env)
#   ./run.sh check 2026-08-14   日付を指定して確認
#   ./run.sh reset          DBを完全削除
#   ./run.sh reset y        非対話で削除
#   ./run.sh migrate        スキーマ変更後、既存DBに不足列を追加
#   ./run.sh batch          日次バッチ (要 .env)
#   ./run.sh batch 2026-08-14   特定日を取り込み
#   ./run.sh backfill       初回バックフィル (既定5年分, 要 .env)
#   ./run.sh backfill 2     過去2年分
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

python=".venv/bin/python"
if [[ ! -x "$python" ]]; then
  echo "先に ./setup.sh を実行してください"
  exit 1
fi

cmd="${1:-serve}"
arg="${2:-}"

# never echo .env values; do not source the file
if [[ -f .env ]]; then
  while IFS= read -r line || [[ -n "$line" ]]; do
    if [[ "$line" =~ ^[[:space:]]*$ ]]; then
      continue
    fi
    if [[ "$line" =~ ^[[:space:]]*# ]]; then
      continue
    fi
    if [[ "$line" =~ ^[[:space:]]*([A-Za-z_][A-Za-z0-9_]*)[[:space:]]*=[[:space:]]*(.*)$ ]]; then
      key="${BASH_REMATCH[1]}"
      value="${BASH_REMATCH[2]}"
      if [[ "$value" =~ ^\"(.*)\"$ ]]; then
        value="${BASH_REMATCH[1]}"
      elif [[ "$value" =~ ^\'(.*)\'$ ]]; then
        value="${BASH_REMATCH[1]}"
      fi
      export "$key"="$value"
    fi
  done < .env
fi

case "$cmd" in
  serve)
    port="${arg:-8000}"
    echo "http://localhost:$port を開いてください"
    "$python" -m uvicorn app.main:app --reload --port "$port"
    ;;
  check)
    if [[ -n "$arg" ]]; then
      "$python" scripts/check_api.py "$arg"
    else
      "$python" scripts/check_api.py
    fi
    ;;
  migrate)
    "$python" scripts/migrate.py
    ;;
  batch)
    "$python" scripts/check_demo.py
    if [[ -n "$arg" ]]; then
      "$python" scripts/run_batch.py --date "$arg"
    else
      "$python" scripts/run_batch.py
    fi
    ;;
  backfill)
    years=5
    if [[ -n "$arg" ]]; then
      if [[ ! "$arg" =~ ^[0-9]+$ ]]; then
        echo "年数は整数で指定してください (例: ./run.sh backfill 2)"
        exit 1
      fi
      years=$((10#$arg))
    fi
    if (( years < 1 || years > 20 )); then
      echo "年数は1〜20の範囲で指定してください"
      exit 1
    fi
    "$python" scripts/check_demo.py
    echo "過去 $years 年分を取得します"
    "$python" scripts/run_batch.py --backfill "$years"
    ;;
  reset)
    if [[ ! -f stock.db ]]; then
      echo "DBはありません。そのまま backfill できます。"
      exit 0
    fi
    ans=""
    if [[ -n "$arg" ]]; then
      ans="$arg"
    elif [[ -t 0 ]]; then
      read -r -p "stock.db を削除します。よろしいですか? (y/N) " ans || ans=""
    else
      echo "中止しました"
      exit 0
    fi
    if [[ "$ans" != "y" ]]; then
      echo "中止しました"
      exit 0
    fi
    rm -f stock.db stock.db-journal stock.db-wal stock.db-shm
    echo "削除しました。次: ./run.sh backfill"
    ;;
  *)
    echo "使い方: ./run.sh [serve|check|reset|migrate|batch|backfill] [引数]"
    exit 1
    ;;
esac
