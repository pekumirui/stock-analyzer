#!/bin/bash
# Restart rclone R2 mount when FUSE is gone but r2-share.service still looks active.
# User systemd timer (stock). Does not write to the mount.
set -u
MOUNT=/home/stock/r2-share
CONF=/home/stock/.config/rclone/rclone.conf
if [[ ! -f "$CONF" ]]; then
  exit 0
fi
if grep -Fq " $MOUNT " /proc/mounts && timeout 10 ls "$MOUNT" >/dev/null 2>&1; then
  exit 0
fi
echo "r2-share-watch: $MOUNT not a live rclone mount; restarting r2-share.service"
systemctl --user restart r2-share.service
