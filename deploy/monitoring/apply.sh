#!/bin/bash
# 監視（VictoriaMetrics / vmalert / Alertmanager / node_exporter / process-exporter）を入れる。
# provision.sh が source して monitoring_apply を呼ぶ。失敗してもアプリの provision は続ける。
# 中身（ユニット・設定・実行ファイル・Webhook）が変わったサービスだけ restart する。
# テスト用: MON_PREFIX（/ の代わり）、MON_SYSTEMCTL、MON_BIN_SRC（fetch の代わりにコピー）。

MON_SERVICES=(victoria-metrics vmalert alertmanager node-exporter process-exporter)

mon_log() {
  echo "monitoring: $*"
}

mon_systemctl() {
  "${MON_SYSTEMCTL:-systemctl}" "$@"
}

mon_paths() {
  local p="${MON_PREFIX:-}"
  MON_SRC="${APP_DIR:?}/deploy/monitoring"
  MON_ETC="$p/etc/monitoring"
  MON_BIN="$p/opt/monitoring/bin"
  MON_UNITS="$p/etc/systemd/system"
  MON_STATE="$p/var/lib/monitoring-apply"
  MON_SLACK_ENV="${MON_SLACK_ENV:-$p/etc/fluent-bit/slack.env}"
}

mon_hash() {
  local f
  for f in "$@"; do
    if [[ -f "$f" ]]; then
      sha256sum "$f" | awk '{print $1}'
    else
      echo missing
    fi
  done | sha256sum | awk '{print $1}'
}

mon_install() {
  local src="$1" dest="$2" mode="$3"
  if [[ -f "$dest" ]] && cmp -s "$src" "$dest"; then
    return 1
  fi
  install -m "$mode" "$src" "$dest"
  mon_log "updated $dest"
  return 0
}

mon_ensure_user() {
  [[ ${EUID} -eq 0 && -z "${MON_PREFIX:-}" ]] || return 0
  if ! getent passwd monitoring >/dev/null; then
    useradd --system --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin monitoring || return 1
    mon_log "created user monitoring"
  fi
}

mon_group_own() {
  [[ ${EUID} -eq 0 && -z "${MON_PREFIX:-}" ]] || return 0
  chown root:monitoring "$@"
}

mon_binaries() {
  local stamp want
  want="$(mon_hash "$MON_SRC/binaries.txt")"
  stamp="$MON_BIN/.binaries.sha256"
  if [[ -z "${MON_BIN_SRC:-}" && -f "$stamp" && "$(cat "$stamp")" == "$want" ]]; then
    return 0
  fi
  install -d -m 755 "$MON_BIN" || return 1
  if [[ -n "${MON_BIN_SRC:-}" ]]; then
    local f
    for f in "$MON_BIN_SRC"/*; do
      mon_install "$f" "$MON_BIN/$(basename "$f")" 755 || true
    done
  else
    bash "$MON_SRC/fetch.sh" "$MON_BIN" "$MON_SRC/binaries.txt" || return 1
  fi
  printf '%s\n' "$want" > "$stamp"
}

mon_configs() {
  install -d -m 755 "$MON_ETC" "$MON_ETC/rules" || return 1
  mon_group_own "$MON_ETC" "$MON_ETC/rules"
  local f
  for f in scrape.yml process-exporter.yml alertmanager.yml; do
    mon_install "$MON_SRC/$f" "$MON_ETC/$f" 644 || true
  done
  for f in "$MON_SRC"/rules/*.yml; do
    mon_install "$f" "$MON_ETC/rules/$(basename "$f")" 644 || true
  done
  for f in "$MON_ETC"/rules/*.yml; do
    [[ -e "$f" ]] || continue
    if [[ ! -f "$MON_SRC/rules/$(basename "$f")" ]]; then
      rm -f "$f"
      mon_log "removed $f"
    fi
  done
}

# slack.env の SLACK_WEBHOOK_URI_INFRA（#alerts-infra。未設定なら provision が #alerts を入れる）。
# 値は出力しない。
mon_slack_url() {
  local out="$MON_ETC/slack_infra_url" uri=""
  if [[ -f "$MON_SLACK_ENV" ]]; then
    uri="$(awk 'index($0, "SLACK_WEBHOOK_URI_INFRA=")==1 {print substr($0, 25); exit}' "$MON_SLACK_ENV")"
  fi
  if [[ "$uri" != /services/* || "$uri" == *[[:space:]]* ]]; then
    mon_log "SLACK_WEBHOOK_URI_INFRA missing in slack.env; alertmanager not started"
    return 1
  fi
  local tmp
  tmp="$(mktemp)"
  printf 'https://hooks.slack.com%s' "$uri" > "$tmp"
  if [[ -f "$out" ]] && cmp -s "$tmp" "$out"; then
    rm -f "$tmp"
    return 0
  fi
  install -m 640 "$tmp" "$out"
  rm -f "$tmp"
  mon_group_own "$out"
  mon_log "updated $out"
}

mon_units() {
  install -d -m 755 "$MON_UNITS" || return 1
  local svc changed=0
  for svc in "${MON_SERVICES[@]}"; do
    if mon_install "$MON_SRC/systemd/monitoring-$svc.service" "$MON_UNITS/monitoring-$svc.service" 644; then
      changed=1
    fi
  done
  if [[ "$changed" == 1 ]]; then
    mon_systemctl daemon-reload || return 1
  fi
}

mon_inputs() {
  local svc="$1"
  local files=("$MON_UNITS/monitoring-$svc.service" "$MON_BIN/.binaries.sha256")
  case "$svc" in
    victoria-metrics) files+=("$MON_ETC/scrape.yml") ;;
    vmalert) files+=("$MON_ETC"/rules/*.yml) ;;
    alertmanager) files+=("$MON_ETC/alertmanager.yml" "$MON_ETC/slack_infra_url") ;;
    process-exporter) files+=("$MON_ETC/process-exporter.yml") ;;
  esac
  printf '%s\n' "${files[@]}"
}

mon_start() {
  local svc="$1" unit="monitoring-$1.service"
  local want have="" mark="$MON_STATE/$svc.sha256"
  local inputs=()
  mapfile -t inputs < <(mon_inputs "$svc")
  want="$(mon_hash "${inputs[@]}")"
  [[ -f "$mark" ]] && have="$(cat "$mark")"
  mon_systemctl enable "$unit" >/dev/null || return 1
  if mon_systemctl is-active --quiet "$unit"; then
    if [[ "$have" == "$want" ]]; then
      return 0
    fi
    mon_systemctl restart "$unit" || return 1
    mon_log "restarted $unit"
  else
    mon_systemctl start "$unit" || return 1
    mon_log "started $unit"
  fi
  printf '%s\n' "$want" > "$mark"
}

monitoring_apply() {
  mon_paths
  mon_ensure_user || return 1
  mon_binaries || { echo "monitoring: binaries failed" >&2; return 1; }
  mon_configs || return 1
  local slack_ok=1
  mon_slack_url || slack_ok=0
  mon_units || return 1
  install -d -m 755 "$MON_STATE" || return 1
  local svc rc=0
  for svc in "${MON_SERVICES[@]}"; do
    if [[ "$svc" == alertmanager && "$slack_ok" != 1 ]]; then
      continue
    fi
    mon_start "$svc" || { echo "monitoring: start $svc failed" >&2; rc=1; }
  done
  return "$rc"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  set -uo pipefail
  monitoring_apply
fi
