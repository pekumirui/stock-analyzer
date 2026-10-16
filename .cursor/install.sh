#!/usr/bin/env bash
# クラウドのエージェントの環境ビルド（ベンダー共通。Cursor は environment.json から呼ぶ）。冪等。
# apt → .venv → tools 依存 → stock.db を用意。
# stock.db が無ければシード DB（data/seed の実データの切り出し。数秒。./run.sh seed）。
# STOCK_DB=full なら代わりに backfill 2（JQUANTS_API_KEY が要る。約 40 分）。
# 既にある stock.db は、シード DB なら触らない。backfill した DB なら日次 batch だけ回す。
# VPS_SSH_KEY があれば本番の monthly_* だけ載せ替える（DB 丸ごとコピーしない）。
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

sudo apt-get update -qq
sudo apt-get install -y -qq --no-install-recommends python3-venv poppler-utils rclone

./setup.sh
.venv/bin/python -m pip install -q -r tools/requirements.txt

# 任意: Secrets / 環境変数の VPS_SSH_KEY があれば stock-vps へ SSH 可能にする
bash .cursor/setup-vps-ssh.sh || echo "setup-vps-ssh skipped"

# Claude Code の自動メモリをリポジトリの .claude/memory/ に向ける（環境が消えても残す）。
# autoMemoryDirectory はコミットする .claude/settings.json では無視されるので settings.local.json に書く
python3 - <<'PY'
import json, pathlib
p = pathlib.Path(".claude/settings.local.json")
s = json.loads(p.read_text()) if p.exists() else {}
s["autoMemoryDirectory"] = str(pathlib.Path(".claude/memory").resolve())
p.write_text(json.dumps(s, ensure_ascii=False, indent=2) + "\n")
PY

is_seed_db() {
  [[ "$(.venv/bin/python -c 'import sqlite3; print(sqlite3.connect("stock.db").execute("PRAGMA user_version").fetchone()[0])')" == "1" ]]
}

if [[ ! -f stock.db ]]; then
  if [[ "${STOCK_DB:-seed}" == "full" ]]; then
    if [[ -z "${JQUANTS_API_KEY:-}" ]]; then
      echo "STOCK_DB=full には JQUANTS_API_KEY が要る" >&2
      exit 1
    fi
    echo "STOCK_DB=full なので backfill 2"
    ./run.sh backfill 2
  else
    echo "stock.db が無いのでシード DB を作る（STOCK_DB=full で backfill）"
    ./run.sh seed
  fi
elif is_seed_db; then
  echo "シード DB なので日次 batch は回さない"
elif [[ -n "${JQUANTS_API_KEY:-}" ]]; then
  echo "stock.db があるので日次 batch だけ回す"
  ./run.sh batch || echo "batch skipped"
else
  echo "JQUANTS_API_KEY が無いので stock.db は触らない"
fi

if [[ -f stock.db ]]; then
  bash .cursor/sync-monthly-from-vps.sh
fi
