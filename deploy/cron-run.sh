#!/bin/bash
set -u
cd /home/stock/stock-analyzer || { echo "fail cron cwd exit=1"; exit 1; }
.venv/bin/python -u "$@"
ec=$?
if [[ $ec -ne 0 ]]; then
  echo "fail cron $* exit=$ec"
fi
exit "$ec"
