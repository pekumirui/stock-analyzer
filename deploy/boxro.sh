# shellcheck shell=bash
# box エージェント向けの読み取り専用 SFTP（KAB-64 / #769）。
# provision.sh から source する。/home/stock/backups・stock.db・.env・R2 FUSE は bind しない。

boxro_defaults() {
  : "${APP_DIR:=/home/stock/stock-analyzer}"
  : "${BOXRO_USER:=boxro}"
  : "${BOXRO_SHELL:=/usr/sbin/nologin}"
  : "${BOXRO_SRV:=/srv}"
  : "${BOXRO_CHROOT:=/srv/boxro}"
  : "${BOXRO_DATA_SRC:=${APP_DIR}/data}"
  : "${BOXRO_LOGS_SRC:=/var/log/app}"
  : "${BOXRO_FSTAB:=/etc/fstab}"
  : "${BOXRO_SSHD_DROPIN:=/etc/ssh/sshd_config.d/90-boxro.conf}"
  : "${BOXRO_AUTH_DIR:=/etc/ssh/authorized_keys}"
  : "${BOXRO_AUTH_DEST:=${BOXRO_AUTH_DIR}/boxro}"
  : "${BOXRO_AUTH_SRC:=${APP_DIR}/deploy/ssh/boxro_authorized_keys}"
  : "${BOXRO_SSHD:=sshd}"
  : "${BOXRO_SYSTEMCTL:=systemctl}"
}

boxro_root_owned() {
  local path="$1" mode="${2:-755}"
  install -d -m "$mode" "$path"
  if [[ ${EUID} -eq 0 ]]; then
    chown root:root "$path"
    chmod "$mode" "$path"
  fi
}

boxro_install_file() {
  local src="$1" dest="$2" mode="${3:-644}"
  if [[ ${EUID} -eq 0 ]]; then
    install -o root -g root -m "$mode" "$src" "$dest"
  else
    install -m "$mode" "$src" "$dest"
  fi
}

boxro_ensure_user() {
  boxro_defaults
  if getent passwd "$BOXRO_USER" >/dev/null 2>&1; then
    echo "boxro: user ${BOXRO_USER} exists"
  else
    useradd --system --shell "$BOXRO_SHELL" --no-create-home "$BOXRO_USER"
    echo "boxro: created user ${BOXRO_USER}"
  fi
  passwd -l "$BOXRO_USER" >/dev/null
}

boxro_ensure_chroot() {
  boxro_defaults
  boxro_root_owned "$BOXRO_SRV" 755
  boxro_root_owned "$BOXRO_CHROOT" 755
  boxro_root_owned "${BOXRO_CHROOT}/data" 755
  boxro_root_owned "${BOXRO_CHROOT}/logs" 755
}

boxro_fstab_line() {
  local src="$1" dest="$2"
  printf '%s %s none bind,ro 0 0' "$src" "$dest"
}

boxro_ensure_fstab_line() {
  local line="$1"
  local fstab dest
  boxro_defaults
  fstab="$BOXRO_FSTAB"
  if [[ ! -e "$fstab" ]]; then
    : > "$fstab"
  fi
  if grep -qxF "$line" "$fstab"; then
    return 0
  fi
  dest="$(awk '{print $2}' <<<"$line")"
  if awk -v d="$dest" '$2 == d { found = 1 } END { exit !found }' "$fstab"; then
    echo "boxro: fstab already has ${dest}"
    return 0
  fi
  if [[ -s "$fstab" ]]; then
    local last
    last="$(tail -c 1 "$fstab" || true)"
    if [[ "$last" != $'\n' ]]; then
      printf '\n' >> "$fstab"
    fi
  fi
  printf '%s\n' "$line" >> "$fstab"
  echo "boxro: fstab added ${line}"
}

boxro_mount_ro_bind() {
  local src="$1" dest="$2"
  if [[ ! -d "$src" ]]; then
    echo "boxro: bind source missing: ${src}" >&2
    return 1
  fi
  install -d -m 755 "$dest"
  if findmnt -n --target "$dest" >/dev/null 2>&1; then
    mount -o remount,bind,ro "$dest"
    echo "boxro: remount ro ${dest}"
    return 0
  fi
  mount --bind "$src" "$dest"
  mount -o remount,bind,ro "$dest"
  echo "boxro: mounted ${src} -> ${dest} (ro bind)"
}

boxro_ensure_binds() {
  boxro_defaults
  local data_dest logs_dest
  data_dest="${BOXRO_CHROOT}/data"
  logs_dest="${BOXRO_CHROOT}/logs"
  boxro_ensure_fstab_line "$(boxro_fstab_line "$BOXRO_DATA_SRC" "$data_dest")"
  boxro_ensure_fstab_line "$(boxro_fstab_line "$BOXRO_LOGS_SRC" "$logs_dest")"
  boxro_mount_ro_bind "$BOXRO_DATA_SRC" "$data_dest"
  boxro_mount_ro_bind "$BOXRO_LOGS_SRC" "$logs_dest"
}

boxro_validate_authorized_keys() {
  local src="$1"
  local keys=0 line
  if [[ ! -f "$src" ]]; then
    echo "boxro: missing authorized_keys source ${src}" >&2
    return 1
  fi
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ -z "${line// }" || "$line" == \#* ]] && continue
    case "$line" in
      restrict[[:space:]]*|restrict,*)
        keys=$((keys + 1))
        ;;
      *)
        echo "boxro: authorized_keys line must start with restrict: ${line}" >&2
        return 1
        ;;
    esac
  done < "$src"
  if [[ "$keys" -lt 1 ]]; then
    echo "boxro: ${src} has no keys" >&2
    return 1
  fi
}

boxro_sync_authorized_keys() {
  boxro_defaults
  boxro_validate_authorized_keys "$BOXRO_AUTH_SRC"
  boxro_root_owned "$BOXRO_AUTH_DIR" 755
  if [[ -f "$BOXRO_AUTH_DEST" ]] && cmp -s "$BOXRO_AUTH_SRC" "$BOXRO_AUTH_DEST"; then
    echo "boxro: authorized_keys unchanged"
    return 0
  fi
  boxro_install_file "$BOXRO_AUTH_SRC" "$BOXRO_AUTH_DEST" 644
  echo "updated ${BOXRO_AUTH_DEST}"
}

boxro_sshd_dropin_body() {
  cat <<'EOF'
Match User boxro
    AuthorizedKeysFile /etc/ssh/authorized_keys/boxro
    ChrootDirectory /srv/boxro
    ForceCommand internal-sftp -R
    AllowTcpForwarding no
    X11Forwarding no
    PermitTTY no
    PasswordAuthentication no
EOF
}

boxro_reload_sshd() {
  boxro_defaults
  if "${BOXRO_SYSTEMCTL}" reload ssh 2>/dev/null; then
    echo "boxro: reloaded ssh"
    return 0
  fi
  if "${BOXRO_SYSTEMCTL}" reload sshd 2>/dev/null; then
    echo "boxro: reloaded sshd"
    return 0
  fi
  echo "boxro: ssh reload failed" >&2
  return 1
}

boxro_apply_sshd_dropin() {
  boxro_defaults
  local dest tmp backup=""
  dest="$BOXRO_SSHD_DROPIN"
  install -d -m 755 "$(dirname "$dest")"
  tmp="$(mktemp)"
  boxro_sshd_dropin_body > "$tmp"
  if [[ -f "$dest" ]] && cmp -s "$tmp" "$dest"; then
    rm -f "$tmp"
    echo "boxro: sshd drop-in unchanged"
    return 0
  fi
  if [[ -f "$dest" ]]; then
    backup="$(mktemp)"
    cp -a "$dest" "$backup"
  fi
  boxro_install_file "$tmp" "$dest" 644
  rm -f "$tmp"
  echo "updated ${dest}"
  if ! "${BOXRO_SSHD}" -t; then
    echo "sshd -t failed; restoring ${dest}" >&2
    if [[ -n "$backup" ]]; then
      boxro_install_file "$backup" "$dest" 644
    else
      rm -f "$dest"
    fi
    rm -f "$backup"
    return 1
  fi
  rm -f "$backup"
  boxro_reload_sshd
}

boxro_apply() {
  boxro_defaults
  boxro_ensure_user
  boxro_ensure_chroot
  boxro_ensure_binds
  boxro_sync_authorized_keys
  boxro_apply_sshd_dropin
}
