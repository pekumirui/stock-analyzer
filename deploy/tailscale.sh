#!/usr/bin/env bash
# shellcheck shell=bash

tailscale_codename() {
  local file="${TAILSCALE_OS_RELEASE:-/etc/os-release}"
  local codename=""
  if [[ ! -f "$file" ]]; then
    echo "tailscale: os-release missing" >&2
    return 1
  fi
  codename="$(
    # shellcheck disable=SC1090
    . "$file"
    printf '%s' "${VERSION_CODENAME:-}"
  )"
  if [[ -z "$codename" ]]; then
    echo "tailscale: VERSION_CODENAME missing" >&2
    return 1
  fi
  printf '%s' "$codename"
}

tailscale_apt_install() {
  if declare -F apt_retry >/dev/null 2>&1; then
    apt_retry tailscale
    return
  fi
  apt-get update -qq || return 1
  apt-get install -y -qq tailscale
}

tailscale_ensure_installed() {
  if command -v tailscale >/dev/null 2>&1; then
    printf 'tailscale present: %s\n' "$(tailscale version 2>/dev/null | head -n1 || true)"
    return 0
  fi
  local codename keyring list key_tmp tmp
  codename="$(tailscale_codename)" || return 1
  keyring="${TAILSCALE_KEYRING:-/usr/share/keyrings/tailscale-archive-keyring.gpg}"
  list="${TAILSCALE_LIST:-/etc/apt/sources.list.d/tailscale.list}"
  install -d -m 755 "$(dirname "$keyring")"
  install -d -m 755 "$(dirname "$list")"
  key_tmp="$(mktemp)"
  if ! curl -fsSL "https://pkgs.tailscale.com/stable/ubuntu/${codename}.noarmor.gpg" -o "$key_tmp"; then
    rm -f "$key_tmp"
    echo "tailscale: keyring download failed" >&2
    return 1
  fi
  install -m 644 "$key_tmp" "$keyring"
  rm -f "$key_tmp"
  tmp="$(mktemp)"
  printf 'deb [signed-by=%s] https://pkgs.tailscale.com/stable/ubuntu %s main\n' "$keyring" "$codename" > "$tmp"
  if [[ ! -f "$list" ]] || ! cmp -s "$tmp" "$list"; then
    install -m 644 "$tmp" "$list"
    echo "tailscale: apt source updated"
  fi
  rm -f "$tmp"
  if ! tailscale_apt_install; then
    echo "tailscale: apt install failed" >&2
    return 1
  fi
  if ! command -v tailscale >/dev/null 2>&1; then
    echo "tailscale: package installed but tailscale is not on PATH" >&2
    return 1
  fi
  echo "tailscale: installed"
}

tailscale_fields() {
  local json
  json="$(tailscale status --json 2>/dev/null || true)"
  if ! printf '%s' "$json" | python3 -c 'import json,sys
raw=sys.stdin.read().strip()
state=""
host=""
data={}
if raw:
    try:
        data=json.loads(raw)
    except Exception:
        data={}
if not isinstance(data, dict):
    data={}
state=str(data.get("BackendState") or "")
node=data.get("Self") or {}
if isinstance(node, dict):
    host=str(node.get("HostName") or "")
sys.stdout.write("state="+state+"\nhost="+host+"\n")'; then
    printf 'state=\nhost=\n'
  fi
}

tailscale_state_joined() {
  case "$1" in
    Running|Starting|Stopped|NeedsMachineAuth) return 0 ;;
    *) return 1 ;;
  esac
}

tailscale_key_present() {
  local xtrace=0 key
  case $- in
    *x*) xtrace=1 ;;
  esac
  set +x
  key="${TAILSCALE_AUTHKEY-}"
  key="${key#"${key%%[![:space:]]*}"}"
  key="${key%"${key##*[![:space:]]}"}"
  if [[ -n "$key" ]]; then
    key=""
    if [[ "$xtrace" -eq 1 ]]; then
      set -x
    fi
    return 0
  fi
  key=""
  if [[ "$xtrace" -eq 1 ]]; then
    set -x
  fi
  return 1
}

tailscale_write_authkey_file() {
  local dest="$1" xtrace=0 key
  case $- in
    *x*) xtrace=1 ;;
  esac
  set +x
  key="${TAILSCALE_AUTHKEY-}"
  key="${key#"${key%%[![:space:]]*}"}"
  key="${key%"${key##*[![:space:]]}"}"
  if [[ -z "$key" ]]; then
    key=""
    if [[ "$xtrace" -eq 1 ]]; then
      set -x
    fi
    return 1
  fi
  printf '%s' "$key" > "$dest"
  key=""
  if [[ "$xtrace" -eq 1 ]]; then
    set -x
  fi
  return 0
}

tailscale_ensure_daemon() {
  if ! command -v systemctl >/dev/null 2>&1; then
    echo "tailscale: systemctl missing; skip daemon enable" >&2
    return 0
  fi
  systemctl enable --now tailscaled
}

tailscale_up() {
  local keyfile rc errexit=0
  case $- in
    *e*) errexit=1 ;;
  esac
  keyfile="$(mktemp)"
  chmod 600 "$keyfile"
  if ! tailscale_write_authkey_file "$keyfile"; then
    rm -f "$keyfile"
    echo "tailscale: auth key unset; skip join"
    return 0
  fi
  set +e
  tailscale up \
    --auth-key="file:${keyfile}" \
    --hostname=stock-vps \
    --accept-dns=false \
    --accept-routes=false \
    --timeout=90s
  rc=$?
  rm -f "$keyfile"
  if [[ "$errexit" -eq 1 ]]; then
    set -e
  fi
  if [[ "$rc" -ne 0 ]]; then
    echo "tailscale: up failed" >&2
    return "$rc"
  fi
  echo "tailscale: joined hostname=stock-vps"
  return 0
}

tailscale_skip_joined() {
  local host="$1"
  if [[ -n "$host" && "$host" != "stock-vps" ]]; then
    echo "tailscale: already joined as ${host}; skip up"
  else
    echo "tailscale: already joined; skip up"
  fi
}

tailscale_capture_status() {
  local -n _state="$1"
  local -n _host="$2"
  local fields
  fields="$(tailscale_fields)"
  _state="${fields#state=}"
  _state="${_state%%$'\n'*}"
  _host=""
  if [[ "$fields" == *$'\nhost='* ]]; then
    _host="${fields#*$'\nhost='}"
  fi
}

tailscale_apply() {
  local state host
  if command -v tailscale >/dev/null 2>&1; then
    tailscale_capture_status state host
    if tailscale_state_joined "$state"; then
      tailscale_skip_joined "$host"
      return 0
    fi
  fi
  if ! tailscale_ensure_installed; then
    if tailscale_key_present; then
      echo "tailscale: install failed" >&2
      return 1
    fi
    echo "tailscale: install failed; auth key unset, skip join" >&2
    return 0
  fi
  if ! tailscale_ensure_daemon; then
    if tailscale_key_present; then
      echo "tailscale: daemon enable failed" >&2
      return 1
    fi
    echo "tailscale: daemon enable failed; auth key unset, skip join" >&2
    return 0
  fi
  tailscale_capture_status state host
  if tailscale_state_joined "$state"; then
    tailscale_skip_joined "$host"
    return 0
  fi
  if ! tailscale_key_present; then
    echo "tailscale: auth key unset; skip join"
    return 0
  fi
  tailscale_up
}
