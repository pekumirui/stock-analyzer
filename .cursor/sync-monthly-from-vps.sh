#!/usr/bin/env bash
# 本番 stock.db の monthly_* だけ Cloud Agent の stock.db へ載せ替える。冪等。
# 本番へは書かない。VPS SSH が無い・繋がらないときは skip（install は落とさない）。
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PY="$ROOT/.cursor/sync_monthly_from_vps.py"
SRC_DB="/home/stock/stock-analyzer/stock.db"
DEST_DB="$ROOT/stock.db"
ID="$$"
REMOTE_PY="/tmp/sync_monthly_from_vps.${ID}.py"
REMOTE_SLICE="/tmp/monthly_slice.${ID}.sqlite"
LOCAL_SLICE="/tmp/monthly_slice.${ID}.sqlite"

cleanup() {
  rm -f "$LOCAL_SLICE"
  ssh -o BatchMode=yes -o ConnectTimeout=8 stock-vps \
    "rm -f '$REMOTE_PY' '$REMOTE_SLICE'" >/dev/null 2>&1 || true
}
trap cleanup EXIT

if [[ ! -f "$DEST_DB" ]]; then
  echo "stock.db が無いので月次は積まない"
  exit 0
fi
if [[ ! -f "$PY" ]]; then
  echo "missing $PY" >&2
  exit 1
fi
if ! ssh -o BatchMode=yes -o ConnectTimeout=8 stock-vps 'echo ok' >/dev/null 2>&1; then
  echo "VPS SSH が使えないので月次は積まない"
  exit 0
fi

scp -q "$PY" "stock-vps:$REMOTE_PY"
ssh -o BatchMode=yes stock-vps \
  "python3 '$REMOTE_PY' dump '$SRC_DB' '$REMOTE_SLICE'"
scp -q "stock-vps:$REMOTE_SLICE" "$LOCAL_SLICE"
python3 "$PY" replace "$DEST_DB" "$LOCAL_SLICE"
echo "monthly sync ok"
