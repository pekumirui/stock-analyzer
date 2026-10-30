#!/bin/bash
set -u
APP_DIR=${CRON_APP_DIR:-/home/stock/stock-analyzer}
LOG_DIR=${CRON_LOG_DIR:-/var/log/app}

job=cron
for arg in "$@"; do
  base=${arg##*/}
  case "$base" in
    *.py)
      job=${base%.py}
      break
      ;;
  esac
done
case "$job" in
  *[!A-Za-z0-9_-]*) job=cron ;;
esac
log="$LOG_DIR/$job.log"

log_line() {
  printf '%s %s\n' "$(TZ=Asia/Tokyo date '+%Y-%m-%d %H:%M:%S')" "$1" >> "$log"
}

mkdir -p "$LOG_DIR" || { echo "fail cron log exit=1" >&2; exit 1; }
if ! touch "$log"; then
  echo "fail cron log exit=1" >&2
  exit 1
fi

cd "$APP_DIR" || { log_line "fail cron cwd exit=1"; exit 1; }

# uvicorn の shebang は python3。デプロイ中に python だけ消える／無いことがある。
py=""
if [[ -x "$APP_DIR/.venv/bin/python3" ]]; then
  py="$APP_DIR/.venv/bin/python3"
elif [[ -x "$APP_DIR/.venv/bin/python" ]]; then
  py="$APP_DIR/.venv/bin/python"
else
  log_line "missing interpreter $APP_DIR/.venv/bin/python3 $APP_DIR/.venv/bin/python"
  log_line "fail cron $* exit=127"
  exit 127
fi

# バッチの CPU / IO を下げて API を優先する。無いコマンドは外す。
# ionice -c3（idle）は使わない。書き込みロックを握ったまま待たされると
# 月次 PUT などが busy_timeout に当たる。uvicorn / MCP はここでは起動しない。
run_py=()
if nice_bin=$(command -v nice 2>/dev/null); then
  run_py+=("$nice_bin" -n 10)
fi
if ionice_bin=$(command -v ionice 2>/dev/null); then
  run_py+=("$ionice_bin" -c2 -n7)
fi
run_py+=("$py" -u)

"${run_py[@]}" "$@" 2>&1 | while IFS= read -r line || [[ -n "${line-}" ]]; do
  printf '%s %s\n' "$(TZ=Asia/Tokyo date '+%Y-%m-%d %H:%M:%S')" "$line"
done >> "$log"
ec=${PIPESTATUS[0]}
if [[ $ec -ne 0 ]]; then
  log_line "fail cron $* exit=$ec"
fi
exit "$ec"
