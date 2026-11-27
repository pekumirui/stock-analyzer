#!/usr/bin/env bash
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
      "$python" tools/market/check_api.py "$arg"
    else
      "$python" tools/market/check_api.py
    fi
    ;;
  migrate)
    "$python" jobs/migrate.py
    ;;
  golden)
    "$python" jobs/golden_db.py
    ;;
  prod)
    shift
    "$python" -m tools.prodslice.load "$@"
    ;;
  serve-prod)
    port="${arg:-8000}"
    echo "http://localhost:$port/app を開いてください（/api は本番へ中継）"
    "$python" -m uvicorn tools.prodslice.proxy:app --port "$port"
    ;;
  batch)
    "$python" jobs/market/check_demo.py
    if [[ -n "$arg" ]]; then
      "$python" jobs/market/run_batch.py --date "$arg"
    else
      "$python" jobs/market/run_batch.py
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
    "$python" jobs/market/check_demo.py
    echo "過去 $years 年分を取得します"
    "$python" jobs/market/run_batch.py --backfill "$years"
    ;;
  reset)
    if [[ ! -f stock.db ]]; then
      echo "DBはありません。そのまま golden / prod / backfill できます。"
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
    echo "削除しました。次: ./run.sh golden（数秒）か ./run.sh backfill"
    ;;
  lint)
    "$python" -m pip install -r requirements-dev.txt
    "$python" -m ruff check --config config/pyproject.toml app jobs tests tools analyzer deploy mcp
    "$python" -m bandit -c config/pyproject.toml -r app jobs tests tools analyzer deploy mcp -x app/xbrlp,.cursor,tmp,.venv
    ;;
  *)
    echo "使い方: ./run.sh [serve|serve-prod|check|reset|migrate|golden|prod|batch|backfill|lint] [引数]"
    exit 1
    ;;
esac
