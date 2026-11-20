#!/usr/bin/env bash
# VPS 上で長いジョブを包んで動かし、終了時に状態ファイルを書いて Grok Bot の webhook に要約を POST する。
# 使い方(VPS上): setsid nohup /home/stock/stock-analyzer/scripts/vps-job-notify.sh <job名> [--after <前のjob名|フォルダ> [--after-timeout 秒] [--after-nonzero] [--after-poll 秒]] -- <コマンド...> </dev/null >/dev/null 2>&1 &
# 出力: ~/jobs/<job名>-<YYYYmmdd-HHMM>/{log,cmd,pid,started,exit,summary,notified}。--after の待ち中は waiting、待ち終えたら after
# webhook 設定(秘密, chmod 600): ~/.config/grok-webhook/env に GROK_WEBHOOK_URL / GROK_WEBHOOK_KEY。無ければ通知せず summary だけ残す。
set -uo pipefail
usage() { echo "usage: vps-job-notify.sh <name> [--after <prev-name|dir>] [--after-timeout sec] [--after-nonzero] [--after-poll sec] -- cmd..." >&2; exit 2; }
name="${1:?job name}"; shift
after=""; after_timeout=86400; after_nonzero=0; after_poll=30
while [ $# -gt 0 ]; do
  case "$1" in
    --) shift; break ;;
    --after) [ $# -ge 2 ] || usage; after="$2"; shift 2 ;;
    --after-timeout) [ $# -ge 2 ] || usage; after_timeout="$2"; shift 2 ;;
    --after-poll) [ $# -ge 2 ] || usage; after_poll="$2"; shift 2 ;;
    --after-nonzero) after_nonzero=1; shift ;;
    *) break ;;
  esac
done
[ $# -gt 0 ] || usage
now() { TZ=Asia/Tokyo date -Is; }

notify() {
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
}

prev=""
if [ -n "$after" ]; then
  if [ -d "$after" ]; then
    prev=$(cd "$after" && pwd)
  else
    prev=$(ls -d ~/jobs/"$after"-[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]-[0-9][0-9][0-9][0-9] 2>/dev/null | tail -n 1)
  fi
fi

d=~/jobs/"$name"-$(TZ=Asia/Tokyo date +%Y%m%d-%H%M); mkdir -p "$d"
printf '%q ' "$@" >"$d/cmd"; echo $$ >"$d/pid"
[ -n "$after" ] || now >"$d/started"
exec 9>"/tmp/vps-job-$name.lock"
if ! flock -n 9; then echo "already running: $name" >"$d/summary"; echo 99 >"$d/exit"; exit 99; fi

not_started() {
  rm -f "$d/waiting"
  echo 98 >"$d/exit"
  {
    echo "【VPSジョブ未起動】$name"
    echo "$1"
    echo "前のジョブ ${prev:-$after}"
    echo "待ち開始 $since / 判定 $(now)"
  } >"$d/summary"
  notify
  exit 98
}

if [ -n "$after" ]; then
  since=$(now)
  [ -n "$prev" ] || not_started "前のジョブのフォルダが見つからない: $after"
  [ "$prev" != "$(cd "$d" && pwd)" ] || not_started "前のジョブが自分と同じフォルダを指している"
  printf '%s\n%s\n' "$prev" "$since" >"$d/waiting"
  SECONDS=0
  while :; do
    p=$(cat "$prev/pid" 2>/dev/null)
    alive=0; [ -n "$p" ] && kill -0 "$p" 2>/dev/null && alive=1
    if [ -s "$prev/exit" ]; then
      prc=$(cat "$prev/exit")
      if [ "$prc" != 0 ] && [ "$after_nonzero" = 0 ]; then
        not_started "前のジョブが exit=$prc で終わったので起動しない（続けるなら --after-nonzero）"
      fi
      break
    fi
    [ "$alive" = 1 ] || not_started "前のジョブが exit を残さずに止まった（pid ${p:-なし} は生きていない）"
    [ "$after_timeout" = 0 ] || [ "$SECONDS" -lt "$after_timeout" ] || not_started "待ちの上限 ${after_timeout} 秒を超えた（前のジョブ pid $p はまだ動いている）"
    sleep "$after_poll"
  done
  printf '%s\nexit=%s\n' "$prev" "$prc" >"$d/after"; rm -f "$d/waiting"
  now >"$d/started"
fi

"$@" >"$d/log" 2>&1 </dev/null; rc=$?
echo "$rc" >"$d/exit"; end=$(now)
{
  echo "【VPSジョブ終了】$name exit=$rc"
  echo "開始 $(cat "$d/started") / 終了 $end"
  [ -z "$prev" ] || echo "前のジョブ $prev (exit=$prc)"
  echo "ログ $d/log"
  echo "--- 最終行 ---"; grep -v '^\s*$' "$d/log" | tail -n 5
  echo "--- エラー件数 ---"
  for p in 'Traceback' 'ERROR' ' 429' ' 503' 'timeout|Timeout'; do printf '%s: %s\n' "$p" "$(grep -cE "$p" "$d/log")"; done
} >"$d/summary"
notify
exit "$rc"
