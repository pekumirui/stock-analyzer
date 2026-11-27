#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${PROD_RO_APP_DIR:-/home/stock/stock-analyzer}"
DB="${PROD_RO_DB:-${APP_DIR}/stock.db}"
PY="${PROD_RO_PYTHON:-${APP_DIR}/.venv/bin/python}"
UPSTREAM="${PROD_RO_UPSTREAM:-http://127.0.0.1:8000}"
CODES_RE='^[0-9][0-9A-Z]{3}[0-9]?(,[0-9][0-9A-Z]{3}[0-9]?){0,49}$'
PATH_RE='^/api/[A-Za-z0-9/_.,:%=&?+~-]*$'
MAX_PATH=2048

die() {
  echo "prod_ro: $*" >&2
  exit 2
}

if (($#)); then
  words=("$@")
else
  read -r -a words <<< "${SSH_ORIGINAL_COMMAND:-}"
fi

[[ ${#words[@]} -eq 2 ]] || die "usage: slice CODES | get /api/..."
verb="${words[0]}"
arg="${words[1]}"

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

case "$verb" in
  slice)
    [[ "$arg" =~ $CODES_RE ]] || die "bad codes: ${arg}"
    (cd "$APP_DIR" && "$PY" -m tools.prodslice.dump "$DB" "$tmp/slice.sqlite" "$arg")
    gzip -c "$tmp/slice.sqlite"
    ;;
  get)
    [[ ${#arg} -le $MAX_PATH ]] || die "path too long"
    [[ "$arg" =~ $PATH_RE ]] || die "bad path: ${arg}"
    [[ "$arg" != *..* ]] || die "bad path: ${arg}"
    head="$(curl -sS --max-time 60 -o "$tmp/body" -w '%{http_code} %{content_type}' "${UPSTREAM}${arg}")"
    printf '%s\n' "$head"
    cat "$tmp/body"
    ;;
  *)
    die "usage: slice CODES | get /api/..."
    ;;
esac
