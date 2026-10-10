#!/bin/bash
# rclone R2 user mount: copy units, then restart only when mount-related files changed.
# Sourced from provision.sh (root). Safe to source in tests (no EUID check).
# Restart/stop of r2-share.service unmounts FUSE and kills in-flight jobs (#549).

r2_file_hash() {
  local f="$1"
  if [[ -f "$f" ]]; then
    sha256sum "$f" | awk '{print $1}'
  else
    printf 'missing\n'
  fi
}

r2_fingerprint() {
  local f
  {
    for f in "$@"; do
      printf '%s %s\n' "$(r2_file_hash "$f")" "$f"
    done
  } | sha256sum | awk '{print $1}'
}

# $1 mount_changed 0|1  $2 active 0|1  → keep | start | restart
r2_share_plan() {
  local mount_changed="${1:-0}"
  local active="${2:-0}"
  if [[ "$mount_changed" == "1" ]]; then
    if [[ "$active" == "1" ]]; then
      printf 'restart\n'
    else
      printf 'start\n'
    fi
    return 0
  fi
  if [[ "$active" != "1" ]]; then
    printf 'start\n'
    return 0
  fi
  printf 'keep\n'
}

r2_copy_unit() {
  local src="$1" dest="$2" mode="${3:-644}"
  if [[ ! -f "$src" ]]; then
    return 1
  fi
  if [[ -f "$dest" ]] && cmp -s "$src" "$dest"; then
    return 1
  fi
  install -m "$mode" "$src" "$dest"
  echo "updated $dest"
  return 0
}

r2_chown() {
  local path="$1"
  [[ -e "$path" ]] || return 0
  chown "${STOCK_USER}:${STOCK_USER}" "$path" 2>/dev/null \
    || chown "$STOCK_USER" "$path" 2>/dev/null \
    || true
}

r2_systemctl() {
  "${R2_SYSTEMCTL:-systemctl}" --user -M "${STOCK_USER}@" "$@"
}

r2_share_apply() {
  local user_sys conf unit_src stamp
  local before_mount after_mount before_watch after_watch
  local stamp_mount stamp_watch mount_changed watch_changed
  local active plan i sysctl_ok

  user_sys="$STOCK_HOME/.config/systemd/user"
  conf="$STOCK_HOME/.config/rclone/rclone.conf"
  stamp="$STOCK_HOME/.config/rclone/r2-share.applied"
  unit_src="$APP_DIR/deploy"

  install -d -m 700 "$STOCK_HOME/.config" "$STOCK_HOME/.config/rclone" \
    "$STOCK_HOME/.config/systemd" "$user_sys" 2>/dev/null || true
  r2_chown "$STOCK_HOME/.config"
  r2_chown "$STOCK_HOME/.config/rclone"
  r2_chown "$STOCK_HOME/.config/systemd"
  r2_chown "$user_sys"

  before_mount=$(r2_fingerprint "$user_sys/r2-share.service" "$conf")
  before_watch=$(r2_fingerprint \
    "$user_sys/r2-share-watch.service" \
    "$user_sys/r2-share-watch.timer" \
    "$unit_src/r2-share-watch.sh")

  r2_copy_unit "$unit_src/r2-share.service" "$user_sys/r2-share.service" 644 || true
  r2_chown "$user_sys/r2-share.service"
  if [[ -f "$unit_src/r2-share-watch.sh" ]]; then
    chmod +x "$unit_src/r2-share-watch.sh"
  fi
  r2_copy_unit "$unit_src/r2-share-watch.service" "$user_sys/r2-share-watch.service" 644 || true
  r2_chown "$user_sys/r2-share-watch.service"
  r2_copy_unit "$unit_src/r2-share-watch.timer" "$user_sys/r2-share-watch.timer" 644 || true
  r2_chown "$user_sys/r2-share-watch.timer"

  after_mount=$(r2_fingerprint "$user_sys/r2-share.service" "$conf")
  after_watch=$(r2_fingerprint \
    "$user_sys/r2-share-watch.service" \
    "$user_sys/r2-share-watch.timer" \
    "$unit_src/r2-share-watch.sh")

  stamp_mount=""
  stamp_watch=""
  if [[ -f "$stamp" ]]; then
    stamp_mount=$(sed -n '1p' "$stamp" | tr -d '[:space:]')
    stamp_watch=$(sed -n '2p' "$stamp" | tr -d '[:space:]')
  fi

  mount_changed=0
  if [[ "$before_mount" != "$after_mount" ]]; then
    mount_changed=1
  fi
  if [[ -n "$stamp_mount" && "$after_mount" != "$stamp_mount" ]]; then
    mount_changed=1
  fi
  watch_changed=0
  if [[ "$before_watch" != "$after_watch" ]]; then
    watch_changed=1
  fi
  if [[ -n "$stamp_watch" && "$after_watch" != "$stamp_watch" ]]; then
    watch_changed=1
  fi

  sysctl_ok=0
  for i in $(seq 1 "${R2_SYSTEMD_READY_TRIES:-15}"); do
    if r2_systemctl list-unit-files >/dev/null 2>&1; then
      sysctl_ok=1
      break
    fi
    sleep "${R2_SYSTEMD_READY_SLEEP:-1}"
  done
  if [[ "$sysctl_ok" != 1 ]]; then
    echo "user systemd for ${STOCK_USER} not ready; r2-share.service not enabled" >&2
    return 0
  fi

  r2_systemctl enable r2-share.service >/dev/null 2>&1 || true
  r2_systemctl enable r2-share-watch.timer >/dev/null 2>&1 || true

  if [[ ! -f "$conf" ]]; then
    echo "r2-share.service enabled but not started (rclone.conf missing; set R2_* secrets and redeploy)"
    return 0
  fi

  active=0
  if r2_systemctl is-active --quiet r2-share.service; then
    active=1
  fi
  plan=$(r2_share_plan "$mount_changed" "$active")

  if [[ "$plan" == "keep" && "$watch_changed" != "1" ]]; then
    echo "r2-share.service unchanged; leave mount running"
    if [[ ! -f "$stamp" ]]; then
      printf '%s\n%s\n' "$after_mount" "$after_watch" > "$stamp"
      r2_chown "$stamp"
    fi
    return 0
  fi

  if [[ "$plan" != "keep" || "$watch_changed" == "1" ]]; then
    r2_systemctl daemon-reload >/dev/null 2>&1 || true
  fi
  if [[ "$plan" == "restart" ]]; then
    echo "r2-share.service config changed; restarting mount"
    r2_systemctl restart r2-share.service >/dev/null 2>&1 \
      || echo "r2-share.service start skipped or failed (conf present)" >&2
  elif [[ "$plan" == "start" ]]; then
    echo "r2-share.service not running; starting mount"
    r2_systemctl start r2-share.service >/dev/null 2>&1 \
      || echo "r2-share.service start skipped or failed (conf present)" >&2
  else
    echo "r2-share.service unchanged; leave mount running"
  fi
  if [[ "$plan" != "keep" || "$watch_changed" == "1" ]]; then
    r2_systemctl start r2-share-watch.timer >/dev/null 2>&1 || true
  fi
  printf '%s\n%s\n' "$after_mount" "$after_watch" > "$stamp"
  r2_chown "$stamp"
  return 0
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  r2_share_apply
fi
