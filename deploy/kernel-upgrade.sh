# shellcheck shell=bash
# provision.sh から source する。
# linux-image-generic と linux-headers-generic だけを --only-upgrade する。
# /var/run/reboot-required があり、~/jobs と cron-run.sh が止まっているときだけ
# shutdown -r +1 する。/etc/cve-2026-98432.nft は読みも消しもしない。

kernel_apt() {
  "${KERNEL_APT_GET:-apt-get}" "$@"
}

kernel_shutdown() {
  "${KERNEL_SHUTDOWN:-shutdown}" "$@"
}

kernel_systemctl() {
  "${KERNEL_SYSTEMCTL:-systemctl}" "$@"
}

kernel_packages_upgrade() {
  export DEBIAN_FRONTEND="${DEBIAN_FRONTEND:-noninteractive}"
  echo "kernel upgrade: linux-image-generic linux-headers-generic"
  kernel_apt -o Acquire::ForceIPv4=true \
    install -y --only-upgrade \
    linux-image-generic \
    linux-headers-generic
}

kernel_jobs_dir() {
  if [[ -n "${KERNEL_JOBS_DIR:-}" ]]; then
    printf '%s' "$KERNEL_JOBS_DIR"
    return 0
  fi
  local home
  home="$(getent passwd "${STOCK_USER:-stock}" 2>/dev/null | cut -d: -f6 || true)"
  if [[ -z "$home" ]]; then
    home="/home/${STOCK_USER:-stock}"
  fi
  printf '%s' "$home/jobs"
}

kernel_proc_root() {
  printf '%s' "${KERNEL_PROC_ROOT:-/proc}"
}

kernel_pid_alive() {
  local pid="$1"
  local proc
  if [[ "$pid" -le 1 ]]; then
    return 1
  fi
  proc="$(kernel_proc_root)"
  if [[ -n "${KERNEL_PROC_ROOT:-}" ]]; then
    [[ -d "$proc/$pid" ]]
    return
  fi
  kill -0 "$pid" 2>/dev/null
}

kernel_is_cron_run() {
  local file="$1/cmdline" arg
  [[ -r "$file" ]] || return 1
  while IFS= read -r -d '' arg || [[ -n "$arg" ]]; do
    case "$arg" in
      */cron-run.sh) return 0 ;;
    esac
  done < "$file"
  return 1
}

kernel_cwd() {
  local path="$1/cwd"
  if [[ -L "$path" ]]; then
    readlink "$path" 2>/dev/null || true
    return 0
  fi
  if [[ -f "$path" ]]; then
    tr -d '\n' < "$path" 2>/dev/null || true
    return 0
  fi
  printf '%s' ""
}

kernel_reboot_blocked() {
  local jobs pidfile pid proc dir cwd
  jobs="$(kernel_jobs_dir)"
  if [[ -d "$jobs" ]]; then
    for pidfile in "$jobs"/*/pid; do
      [[ -f "$pidfile" ]] || continue
      pid="$(tr -cd '0-9' < "$pidfile")"
      [[ -n "$pid" ]] || continue
      if kernel_pid_alive "$pid"; then
        return 0
      fi
    done
  fi
  proc="$(kernel_proc_root)"
  for dir in "$proc"/[0-9]*; do
    [[ -d "$dir" ]] || continue
    if kernel_is_cron_run "$dir"; then
      return 0
    fi
    if [[ -n "$jobs" ]]; then
      cwd="$(kernel_cwd "$dir")"
      if [[ "$cwd" == "$jobs" || "$cwd" == "$jobs"/* ]]; then
        return 0
      fi
    fi
  done
  return 1
}

kernel_reboot_already_scheduled() {
  local marker="${KERNEL_SHUTDOWN_STATE:-/run/systemd/shutdown/scheduled}"
  [[ -e "$marker" ]]
}

kernel_ensure_services() {
  local unit cve_unit
  for unit in stock-analyzer stock-analyzer-mcp; do
    if kernel_systemctl is-enabled --quiet "$unit"; then
      continue
    fi
    kernel_systemctl enable "$unit"
  done
  cve_unit="${KERNEL_CVE_UNIT:-/etc/systemd/system/cve-2026-98432.service}"
  if [[ -f "$cve_unit" ]]; then
    if ! kernel_systemctl is-enabled --quiet cve-2026-98432.service; then
      kernel_systemctl enable cve-2026-98432.service
    fi
  fi
}

kernel_maybe_reboot() {
  local required="${KERNEL_REBOOT_REQUIRED:-/var/run/reboot-required}"
  if [[ ! -f "$required" ]]; then
    echo "reboot not required"
    return 0
  fi
  if kernel_reboot_blocked; then
    echo "reboot pending"
    return 0
  fi
  if kernel_reboot_already_scheduled; then
    echo "reboot already scheduled"
    return 0
  fi
  kernel_ensure_services
  kernel_shutdown -r +1
  echo "reboot scheduled: shutdown -r +1"
}
