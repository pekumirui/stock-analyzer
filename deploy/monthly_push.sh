# shellcheck shell=bash
# box からの月次 PDF 追加専用 rsync（KAB-135 / #900）。
# provision.sh から source する。stock の authorized_keys に restrict + rrsync -wo を
# 冪等追加するだけ。書き込み先は data/monthly。既存の admin / デプロイ鍵・boxro は触らない。

monthly_push_defaults() {
  : "${APP_DIR:=/home/stock/stock-analyzer}"
  : "${MONTHLY_PUSH_USER:=stock}"
  : "${MONTHLY_PUSH_DATA_SRC:=${APP_DIR}/data}"
  : "${MONTHLY_PUSH_DEST:=${APP_DIR}/data/monthly}"
  : "${MONTHLY_PUSH_RRSYNC:=/usr/bin/rrsync}"
  : "${MONTHLY_PUSH_AUTH_SRC:=${APP_DIR}/deploy/ssh/monthly_push_authorized_keys}"
  : "${MONTHLY_PUSH_AUTH_DEST:=}"
}

monthly_push_auth_dest() {
  monthly_push_defaults
  if [[ -n "${MONTHLY_PUSH_AUTH_DEST}" ]]; then
    printf '%s\n' "$MONTHLY_PUSH_AUTH_DEST"
    return 0
  fi
  local home
  home="$(getent passwd "$MONTHLY_PUSH_USER" | cut -d: -f6)"
  if [[ -z "$home" ]]; then
    echo "monthly_push: no home for ${MONTHLY_PUSH_USER}" >&2
    return 1
  fi
  printf '%s\n' "${home}/.ssh/authorized_keys"
}

monthly_push_pubkey_blob() {
  local line="$1"
  if [[ "$line" =~ (ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp256)[[:space:]]+([A-Za-z0-9+/=]+) ]]; then
    printf '%s %s' "${BASH_REMATCH[1]}" "${BASH_REMATCH[2]}"
    return 0
  fi
  return 1
}

monthly_push_command_from_line() {
  local line="$1"
  if [[ "$line" =~ command=\"([^\"]+)\" ]]; then
    printf '%s' "${BASH_REMATCH[1]}"
    return 0
  fi
  return 1
}

monthly_push_validate_command() {
  local cmd="$1"
  local -a args
  local dest bin wo i
  if [[ "$cmd" == *['$`;|&<>()']* ]]; then
    echo "monthly_push: command has shell metacharacters: ${cmd}" >&2
    return 1
  fi
  read -r -a args <<< "$cmd"
  if [[ ${#args[@]} -lt 2 ]]; then
    echo "monthly_push: command must be rrsync + dest: ${cmd}" >&2
    return 1
  fi
  bin="${args[0]}"
  dest="${args[${#args[@]} - 1]}"
  dest="${dest%/}"
  if [[ "$bin" != /usr/bin/rrsync ]]; then
    echo "monthly_push: command binary must be /usr/bin/rrsync: ${cmd}" >&2
    return 1
  fi
  if [[ "$dest" != */data/monthly ]]; then
    echo "monthly_push: rrsync dest must end with /data/monthly: ${cmd}" >&2
    return 1
  fi
  if [[ "$dest" == *..* ]]; then
    echo "monthly_push: rrsync dest must not contain ..: ${cmd}" >&2
    return 1
  fi
  wo=0
  for ((i = 1; i < ${#args[@]} - 1; i++)); do
    case "${args[i]}" in
      -wo|--write-only) wo=1 ;;
      -ro|--read-only)
        echo "monthly_push: rrsync must not be read-only: ${cmd}" >&2
        return 1
        ;;
    esac
  done
  if [[ "$wo" -ne 1 ]]; then
    echo "monthly_push: rrsync must include -wo: ${cmd}" >&2
    return 1
  fi
}

monthly_push_validate_line() {
  local line="$1"
  local cmd
  case "$line" in
    restrict[[:space:]]*|restrict,*)
      ;;
    *)
      echo "monthly_push: authorized_keys line must start with restrict: ${line}" >&2
      return 1
      ;;
  esac
  cmd="$(monthly_push_command_from_line "$line")" || {
    echo "monthly_push: authorized_keys line must have command=: ${line}" >&2
    return 1
  }
  monthly_push_validate_command "$cmd" || return 1
  monthly_push_pubkey_blob "$line" >/dev/null || {
    echo "monthly_push: authorized_keys line has no pubkey: ${line}" >&2
    return 1
  }
}

monthly_push_validate_authorized_keys() {
  local src="$1"
  local keys=0 line
  if [[ ! -f "$src" ]]; then
    echo "monthly_push: missing authorized_keys source ${src}" >&2
    return 1
  fi
  if grep -q 'PRIVATE KEY' "$src"; then
    echo "monthly_push: secret key material must not be in ${src}" >&2
    return 1
  fi
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ -z "${line// }" || "$line" == \#* ]] && continue
    monthly_push_validate_line "$line" || return 1
    keys=$((keys + 1))
  done < "$src"
  if [[ "$keys" -lt 1 ]]; then
    echo "monthly_push: ${src} has no keys" >&2
    return 1
  fi
}

monthly_push_ensure_rrsync() {
  monthly_push_defaults
  if [[ -x "$MONTHLY_PUSH_RRSYNC" ]]; then
    echo "monthly_push: rrsync ${MONTHLY_PUSH_RRSYNC}"
    return 0
  fi
  echo "monthly_push: missing ${MONTHLY_PUSH_RRSYNC} (install rsync)" >&2
  return 1
}

monthly_push_ensure_dest() {
  monthly_push_defaults
  if [[ ! -d "$MONTHLY_PUSH_DATA_SRC" ]]; then
    echo "monthly_push: missing ${MONTHLY_PUSH_DATA_SRC}" >&2
    return 1
  fi
  if [[ ${EUID} -eq 0 ]]; then
    install -d -m 755 -o "$MONTHLY_PUSH_USER" -g "$MONTHLY_PUSH_USER" "$MONTHLY_PUSH_DEST"
  else
    install -d -m 755 "$MONTHLY_PUSH_DEST"
  fi
  echo "monthly_push: dest ${MONTHLY_PUSH_DEST}"
}

monthly_push_ensure_auth_file() {
  local dest="$1" user="$2"
  local dir
  dir="$(dirname "$dest")"
  if [[ ${EUID} -eq 0 ]]; then
    install -d -m 700 -o "$user" -g "$user" "$dir"
    if [[ ! -f "$dest" ]]; then
      install -o "$user" -g "$user" -m 600 /dev/null "$dest"
    else
      chown "$user:$user" "$dest"
      chmod 600 "$dest"
    fi
  else
    install -d -m 700 "$dir"
    if [[ ! -f "$dest" ]]; then
      install -m 600 /dev/null "$dest"
    else
      chmod 600 "$dest"
    fi
  fi
}

monthly_push_append_line() {
  local dest="$1" line="$2"
  local last
  if [[ -s "$dest" ]]; then
    last="$(tail -c 1 "$dest" || true)"
    if [[ "$last" != $'\n' ]]; then
      printf '\n' >> "$dest"
    fi
  fi
  printf '%s\n' "$line" >> "$dest"
}

monthly_push_sync_authorized_keys() {
  monthly_push_defaults
  monthly_push_validate_authorized_keys "$MONTHLY_PUSH_AUTH_SRC" || return 1
  local dest
  dest="$(monthly_push_auth_dest)" || return 1
  monthly_push_ensure_auth_file "$dest" "$MONTHLY_PUSH_USER" || return 1
  restricted_keys_append "$MONTHLY_PUSH_AUTH_SRC" "$dest" monthly_push
}

restricted_keys_append() {
  local src="$1" dest="$2" tag="$3"
  local line existing blob existing_blob found_exact found_blob added
  added=0
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ -z "${line// }" || "$line" == \#* ]] && continue
    found_exact=0
    found_blob=0
    blob="$(monthly_push_pubkey_blob "$line")" || {
      echo "${tag}: cannot parse pubkey: ${line}" >&2
      return 1
    }
    while IFS= read -r existing || [[ -n "$existing" ]]; do
      [[ -z "${existing// }" || "$existing" == \#* ]] && continue
      if [[ "$existing" == "$line" ]]; then
        found_exact=1
        continue
      fi
      existing_blob="$(monthly_push_pubkey_blob "$existing" || true)"
      if [[ -n "$existing_blob" && "$existing_blob" == "$blob" ]]; then
        found_blob=1
      fi
    done < "$dest"
    if [[ "$found_exact" -eq 1 ]]; then
      continue
    fi
    if [[ "$found_blob" -eq 1 ]]; then
      echo "${tag}: key already present with different options in ${dest}" >&2
      return 1
    fi
    monthly_push_append_line "$dest" "$line"
    added=$((added + 1))
    echo "${tag}: added key to ${dest}"
  done < "$src"
  if [[ "$added" -eq 0 ]]; then
    echo "${tag}: authorized_keys unchanged"
  fi
}

monthly_push_apply() {
  monthly_push_defaults
  monthly_push_ensure_rrsync || return 1
  monthly_push_ensure_dest || return 1
  monthly_push_sync_authorized_keys || return 1
}
