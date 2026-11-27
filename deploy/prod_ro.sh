# shellcheck shell=bash
# shellcheck source=monthly_push.sh
# shellcheck disable=SC1091
source "$(dirname "${BASH_SOURCE[0]}")/monthly_push.sh"

prod_ro_defaults() {
  : "${APP_DIR:=/home/stock/stock-analyzer}"
  : "${PROD_RO_USER:=stock}"
  : "${PROD_RO_COMMAND:=${APP_DIR}/deploy/prod_ro_command.sh}"
  : "${PROD_RO_AUTH_SRC:=${APP_DIR}/deploy/ssh/prod_ro_authorized_keys}"
  : "${PROD_RO_AUTH_DEST:=}"
}

prod_ro_auth_dest() {
  prod_ro_defaults
  if [[ -n "${PROD_RO_AUTH_DEST}" ]]; then
    printf '%s\n' "$PROD_RO_AUTH_DEST"
    return 0
  fi
  local home
  home="$(getent passwd "$PROD_RO_USER" | cut -d: -f6)"
  if [[ -z "$home" ]]; then
    echo "prod_ro: no home for ${PROD_RO_USER}" >&2
    return 1
  fi
  printf '%s\n' "${home}/.ssh/authorized_keys"
}

prod_ro_validate_line() {
  prod_ro_defaults
  local line="$1" opts cmd opt
  local -a parts
  opts="${line%% ssh-*}"
  opts="${opts%% ecdsa-*}"
  if [[ "$opts" != restrict,* ]]; then
    echo "prod_ro: authorized_keys line must start with restrict,command=: ${line}" >&2
    return 1
  fi
  cmd="$(monthly_push_command_from_line "$line")" || {
    echo "prod_ro: authorized_keys line must have command=: ${line}" >&2
    return 1
  }
  if [[ "$cmd" != "$PROD_RO_COMMAND" ]]; then
    echo "prod_ro: command must be exactly ${PROD_RO_COMMAND}: ${cmd}" >&2
    return 1
  fi
  IFS=',' read -r -a parts <<< "${opts//\"${cmd}\"/}"
  for opt in "${parts[@]}"; do
    case "$opt" in
      restrict|command=|from=\"*\") ;;
      *)
        echo "prod_ro: option not allowed: ${opt}" >&2
        return 1
        ;;
    esac
  done
  monthly_push_pubkey_blob "$line" >/dev/null || {
    echo "prod_ro: authorized_keys line has no pubkey: ${line}" >&2
    return 1
  }
}

prod_ro_validate_authorized_keys() {
  local src="$1" keys=0 line
  if [[ ! -f "$src" ]]; then
    echo "prod_ro: missing authorized_keys source ${src}" >&2
    return 1
  fi
  if grep -q 'PRIVATE KEY' "$src"; then
    echo "prod_ro: secret key material must not be in ${src}" >&2
    return 1
  fi
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ -z "${line// }" || "$line" == \#* ]] && continue
    prod_ro_validate_line "$line" || return 1
    keys=$((keys + 1))
  done < "$src"
  if [[ "$keys" -lt 1 ]]; then
    echo "prod_ro: ${src} has no keys" >&2
    return 1
  fi
}

prod_ro_apply() {
  prod_ro_defaults
  local dest
  if [[ ! -x "$PROD_RO_COMMAND" ]]; then
    echo "prod_ro: missing executable ${PROD_RO_COMMAND}" >&2
    return 1
  fi
  prod_ro_validate_authorized_keys "$PROD_RO_AUTH_SRC" || return 1
  dest="$(prod_ro_auth_dest)" || return 1
  monthly_push_ensure_auth_file "$dest" "$PROD_RO_USER" || return 1
  restricted_keys_append "$PROD_RO_AUTH_SRC" "$dest" prod_ro
}
