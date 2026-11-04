#!/usr/bin/env bash
# VPS 上で長いジョブを包んで動かし、終了時に状態ファイルを書いて Grok Bot の webhook に要約を POST する。
# 使い方(VPS上): setsid nohup /home/stock/stock-analyzer/scripts/vps-job-notify.sh <job名> -- <コマンド...> </dev/null >/dev/null 2>&1 &
# 出力: ~/jobs/<job名>-<YYYYmmdd-HHMM>/{log,cmd,pid,started,exit,summary}
# webhook 設定(秘密, chmod 600): ~/.config/grok-webhook/env に GROK_WEBHOOK_URL / GROK_WEBHOOK_KEY。無ければ通知せず summary だけ残す。
set -uo pipefail
name="${1:?job name}"; shift; [ "${1:-}" = "--" ] && shift
[ $# -gt 0 ] || { echo "usage: vps-job-notify.sh <name> -- cmd..." >&2; exit 2; }
d=~/jobs/"$name"-$(TZ=Asia/Tokyo date +%Y%m%d-%H%M); mkdir -p "$d"
printf '%q ' "$@" >"$d/cmd"; echo $$ >"$d/pid"; TZ=Asia/Tokyo date -Is >"$d/started"
exec 9>"/tmp/vps-job-$name.lock"
if ! flock -n 9; then echo "already running: $name" >"$d/summary"; echo 99 >"$d/exit"; exit 99; fi
"$@" >"$d/log" 2>&1 </dev/null; rc=$?
echo "$rc" >"$d/exit"; end=$(TZ=Asia/Tokyo date -Is)
{
  echo "【VPSジョブ終了】$name exit=$rc"
  echo "開始 $(cat "$d/started") / 終了 $end"
  echo "ログ $d/log"
  echo "--- 最終行 ---"; grep -v '^\s*$' "$d/log" | tail -n 5
  echo "--- エラー件数 ---"
  for p in 'Traceback' 'ERROR' ' 429' ' 503' 'timeout|Timeout'; do printf '%s: %s\n' "$p" "$(grep -cE "$p" "$d/log")"; done
} >"$d/summary"
CONF=~/.config/grok-webhook/env
if [ -r "$CONF" ]; then
  . "$CONF"
  body=$(python3 -c 'import json,sys;print(json.dumps({"from":"vps-job","kind":"report","text":open(sys.argv[1]).read()[:3800]},ensure_ascii=False))' "$d/summary")
  code=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 15 --retry 3 -X POST "$GROK_WEBHOOK_URL" \
    -H 'Content-Type: application/json' -H "Authorization: Bearer $GROK_WEBHOOK_KEY" -H "X-Automation-Key: $GROK_WEBHOOK_KEY" \
    --data-binary "$body") || code=000
  echo "$code" >"$d/notified"
else
  echo "no-webhook-config" >"$d/notified"
fi
exit "$rc"
