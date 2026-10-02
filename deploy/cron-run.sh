#!/bin/bash
# stdout/stderr を /var/log/app/<stem>.log へ。行頭は JST。ジョブの TZ は変えない。
# CRON_APP_DIR / CRON_LOG_DIR はテスト用。本番 cron は未設定。
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

"$py" -u "$@" 2>&1 | while IFS= read -r line || [[ -n "${line-}" ]]; do
  printf '%s %s\n' "$(TZ=Asia/Tokyo date '+%Y-%m-%d %H:%M:%S')" "$line"
done >> "$log"
ec=${PIPESTATUS[0]}
if [[ $ec -ne 0 ]]; then
  log_line "fail cron $* exit=$ec"
fi
exit "$ec"
