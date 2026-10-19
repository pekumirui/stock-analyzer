#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "root で実行してください（sudo deploy/provision.sh）" >&2
  exit 1
fi

APP_DIR="${APP_DIR:-/home/stock/stock-analyzer}"

write_if_changed() {
  local dest="$1" mode="${2:-644}"
  local tmp
  tmp="$(mktemp)"
  cat > "$tmp"
  if [[ ! -f "$dest" ]] || ! cmp -s "$tmp" "$dest"; then
    install -m "$mode" "$tmp" "$dest"
    echo "updated $dest"
    rm -f "$tmp"
    return 0
  fi
  rm -f "$tmp"
  return 0
}

install_sudoers() {
  if [[ -f "$APP_DIR/deploy/sudoers" ]]; then
    if visudo -cf "$APP_DIR/deploy/sudoers" >/dev/null; then
      write_if_changed /etc/sudoers.d/stock-analyzer 440 < "$APP_DIR/deploy/sudoers" || true
    else
      echo "sudoers の visudo 検証に失敗したので更新しません" >&2
      exit 1
    fi
  fi
}

# Live /etc/sudoers.d lags the repo. update.sh installs this copy before
# --preserve-env names that the previous sudoers did not env_keep.
if [[ "${1:-}" == "--sudoers-only" ]]; then
  install_sudoers
  echo "sudoers ok"
  exit 0
fi

STOCK_USER="${STOCK_USER:-stock}"
DOMAIN="${VPS_DOMAIN:-160.251.179.50.sslip.io}"
DOMAIN="${DOMAIN#"${DOMAIN%%[![:space:]]*}"}"
DOMAIN="${DOMAIN%"${DOMAIN##*[![:space:]]}"}"
DOMAIN="${DOMAIN#http://}"
DOMAIN="${DOMAIN#https://}"
DOMAIN="${DOMAIN%%/*}"
DOMAIN="${DOMAIN//$'\r'/}"
echo "cert domain_len=${#DOMAIN}"
getent hosts "$DOMAIN" | awk '{print "cert resolve", $1}' || echo "cert resolve failed"
WWW=""
if [[ "$DOMAIN" != www.* && "$DOMAIN" != *.sslip.io ]]; then
  WWW="www.${DOMAIN}"
fi
SERVER_NAMES="${DOMAIN}${WWW:+ ${WWW}} _"
CERT_LIVE="/etc/letsencrypt/live/${DOMAIN}"
HTPASSWD="/etc/nginx/stock-analyzer.htpasswd"
ACME_ROOT="/var/www/letsencrypt"
export DEBIAN_FRONTEND=noninteractive

apt_retry() {
  # security.ubuntu.com の IPv6 ミラーが古い .deb を指して 404 になることがある。
  apt-get -o Acquire::ForceIPv4=true update -qq
  if apt-get -o Acquire::ForceIPv4=true install -y -qq "$@"; then
    return 0
  fi
  echo "apt install failed; retry after update --fix-missing" >&2
  apt-get -o Acquire::ForceIPv4=true update -qq
  apt-get -o Acquire::ForceIPv4=true install -y -qq --fix-missing "$@"
}

apt_retry \
  python3-venv python3-pip git curl gnupg ca-certificates \
  nginx apache2-utils certbot \
  ufw tzdata fuse3 unzip poppler-utils

timedatectl set-timezone Asia/Tokyo

if ! command -v rclone >/dev/null 2>&1; then
  curl -fsSL https://rclone.org/install.sh | bash || echo "rclone install failed (R2 mount skipped)" >&2
else
  echo "rclone present: $(rclone version 2>/dev/null | head -n1)"
fi

if command -v fluent-bit >/dev/null 2>&1 || [[ -x /opt/fluent-bit/bin/fluent-bit ]]; then
  echo "fluent-bit present"
else
  if ! (
    install -d -m 755 /usr/share/keyrings
    key_tmp="$(mktemp)"
    curl -fsSL https://packages.fluentbit.io/fluentbit.key | gpg --dearmor > "$key_tmp"
    install -m 644 "$key_tmp" /usr/share/keyrings/fluentbit-keyring.gpg
    rm -f "$key_tmp"
    codename="$(grep -oP '(?<=VERSION_CODENAME=).*' /etc/os-release 2>/dev/null || lsb_release -cs)"
    write_if_changed /etc/apt/sources.list.d/fluent-bit.list 644 <<EOF
deb [signed-by=/usr/share/keyrings/fluentbit-keyring.gpg] https://packages.fluentbit.io/ubuntu/${codename} ${codename} main
EOF
    apt_retry fluent-bit
  ); then
    echo "fluent-bit install failed (alerts skipped)" >&2
  fi
fi

install_authorized_key() {
  local home="$1" user="$2" pubkey="$3"
  install -d -m 700 -o "$user" -g "$user" "$home/.ssh"
  local auth="$home/.ssh/authorized_keys"
  touch "$auth"
  chown "$user:$user" "$auth"
  chmod 600 "$auth"
  if ! grep -qxF "$pubkey" "$auth"; then
    printf '%s\n' "$pubkey" >> "$auth"
    echo "authorized_keys: added key for $user"
  fi
}

if [[ -f "$APP_DIR/deploy/admin_keys" ]]; then
  stock_home="$(getent passwd "$STOCK_USER" | cut -d: -f6)"
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ -z "${line// }" || "$line" == \#* ]] && continue
    install_authorized_key /root root "$line"
    if [[ -n "$stock_home" ]]; then
      install_authorized_key "$stock_home" "$STOCK_USER" "$line"
    fi
  done < "$APP_DIR/deploy/admin_keys"
fi

# cloud-init の drop-in より後になる番号。パスワード認証は使わない。
write_if_changed /etc/ssh/sshd_config.d/99-key-only.conf 644 <<'EOF'
PermitRootLogin prohibit-password
PubkeyAuthentication yes
PasswordAuthentication no
KbdInteractiveAuthentication no
EOF
sshd -t
systemctl reload ssh 2>/dev/null || systemctl reload sshd 2>/dev/null || true

install -d -m 755 "$ACME_ROOT"
usermod -aG systemd-journal,adm "$STOCK_USER" 2>/dev/null || true

LOG_DIR="/var/log/app"
install -d -m 755 -o "$STOCK_USER" -g "$STOCK_USER" "$LOG_DIR"
for f in stock-analyzer.log mcp.log batch.log; do
  if [[ ! -f "$LOG_DIR/$f" ]]; then
    install -m 640 -o "$STOCK_USER" -g "$STOCK_USER" /dev/null "$LOG_DIR/$f"
  else
    chown "$STOCK_USER:$STOCK_USER" "$LOG_DIR/$f"
    chmod 640 "$LOG_DIR/$f"
  fi
done
# cron-run.sh の出力先。先に空ファイルを作ると fluent-bit が末尾から追える。
if [[ -f "$APP_DIR/deploy/crontab" ]]; then
  scripts="$(grep -oE 'jobs/([A-Za-z0-9_]+/)*[A-Za-z0-9_]+\.py' "$APP_DIR/deploy/crontab" | sort -u || true)"
  while IFS= read -r script; do
    [[ -z "$script" ]] && continue
    stem="${script##*/}"
    stem="${stem%.py}"
    f="$LOG_DIR/$stem.log"
    if [[ ! -f "$f" ]]; then
      install -m 640 -o "$STOCK_USER" -g "$STOCK_USER" /dev/null "$f"
    else
      chown "$STOCK_USER:$STOCK_USER" "$f"
      chmod 640 "$f"
    fi
  done <<< "$scripts"
fi
# ジョブ分割前の APP_DIR/batch.log。rotate で LOG_DIR 側が空になると
# 再コピーして fluent-bit が古い traceback を page するので、移さず消す。
if [[ -e "$APP_DIR/batch.log" ]]; then
  rm -f "$APP_DIR/batch.log"
  echo "removed leftover $APP_DIR/batch.log"
fi
if [[ -f "$APP_DIR/deploy/logrotate" ]]; then
  write_if_changed /etc/logrotate.d/stock-analyzer 644 < "$APP_DIR/deploy/logrotate"
fi

# boxro: data だけを読む SFTP。既存の ssh / cron / サービスは触らない。
# 失敗してもアプリ側の provision（systemd / nginx / crontab）は続ける。
# shellcheck source=boxro.sh
# shellcheck disable=SC1091
source "$APP_DIR/deploy/boxro.sh"
if ! boxro_apply; then
  echo "boxro: apply failed (sftp jail incomplete; app deploy continues)" >&2
fi

if write_if_changed /etc/systemd/system/stock-analyzer.service 644 < "$APP_DIR/deploy/stock-analyzer.service"; then
  systemctl daemon-reload
fi
systemctl enable stock-analyzer >/dev/null

if write_if_changed /etc/systemd/system/stock-analyzer-mcp.service 644 < "$APP_DIR/deploy/stock-analyzer-mcp.service"; then
  systemctl daemon-reload
fi
systemctl enable stock-analyzer-mcp >/dev/null

install_sudoers

if [[ -n "${BASIC_AUTH_USER:-}" && -n "${BASIC_AUTH_PASSWORD:-}" ]]; then
  touch "$HTPASSWD"
  htpasswd -bB "$HTPASSWD" "$BASIC_AUTH_USER" "$BASIC_AUTH_PASSWORD" >/dev/null
  chmod 640 "$HTPASSWD"
  chown root:www-data "$HTPASSWD"
  echo "htpasswd ok (${BASIC_AUTH_USER})"
fi

# shellcheck source=nginx-site.sh
# shellcheck disable=SC1091
source "$APP_DIR/deploy/nginx-site.sh"
write_if_changed /etc/nginx/conf.d/stock-analyzer-limit.conf 644 < <(render_limit_conf)
write_if_changed /etc/nginx/conf.d/stock-analyzer-log.conf 644 < <(render_log_conf)

nginx_changed=0
if [[ -f "${CERT_LIVE}/fullchain.pem" ]]; then
  write_if_changed /etc/nginx/sites-available/stock-analyzer 644 < <(render_https) || true
else
  write_if_changed /etc/nginx/sites-available/stock-analyzer 644 < <(render_http_only) || true
fi

rm -f /etc/nginx/sites-enabled/default
ln -sfn /etc/nginx/sites-available/stock-analyzer /etc/nginx/sites-enabled/stock-analyzer
nginx -t
systemctl enable nginx >/dev/null
systemctl reload nginx 2>/dev/null || systemctl start nginx

ufw allow OpenSSH >/dev/null || ufw allow 22/tcp >/dev/null
ufw allow 80/tcp >/dev/null
ufw allow 443/tcp >/dev/null
if ! ufw status | grep -qi "Status: active"; then
  echo "y" | ufw --force enable >/dev/null
fi

if [[ -n "$DOMAIN" && "$DOMAIN" != "_" ]]; then
  certbot_opts=(--non-interactive --agree-tos)
  if [[ -n "${CERTBOT_EMAIL:-}" ]]; then
    certbot_opts+=(--email "$CERTBOT_EMAIL")
  else
    certbot_opts+=(--register-unsafely-without-email)
  fi
  install -d -m 755 /etc/letsencrypt/renewal-hooks/deploy
  write_if_changed /etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh 755 <<'EOF'
#!/bin/bash
systemctl reload nginx
EOF
  systemctl enable --now certbot.timer >/dev/null
  cert_names=(-d "$DOMAIN")
  if [[ -n "$WWW" ]]; then
    cert_names+=(-d "$WWW")
  fi
  echo "certbot: issue ${DOMAIN}${WWW:+ + ${WWW}}"
  # ECDSA 既定は YE1 → Root YE。Root YE はまだ OS の信頼ストアに無く、
  # ブラウザが「セキュリティ保護なし」になる。RSA + ISRG Root X1 にする。
  certbot certonly --webroot -w "$ACME_ROOT" --expand --cert-name "$DOMAIN" \
    --key-type rsa --rsa-key-size 2048 \
    "${cert_names[@]}" "${certbot_opts[@]}"
  write_if_changed /etc/nginx/sites-available/stock-analyzer 644 < <(render_https) || true
  nginx -t
  systemctl reload nginx
fi

if [[ -f "$APP_DIR/deploy/cron-run.sh" ]]; then
  chmod +x "$APP_DIR/deploy/cron-run.sh"
fi
STOCK_HOME="$(getent passwd "$STOCK_USER" | cut -d: -f6)"
if [[ -n "$STOCK_HOME" ]]; then
  install -d -m 750 -o "$STOCK_USER" -g "$STOCK_USER" "$STOCK_HOME/backups"
fi
crontab -u "$STOCK_USER" "$APP_DIR/deploy/crontab"

STOCK_HOME="$(getent passwd "$STOCK_USER" | cut -d: -f6)"
if [[ -z "$STOCK_HOME" ]]; then
  echo "stock home not found; skip R2 mount" >&2
else
  # rclone FUSE without allow_other is invisible to root via [[ -e ]]; use /proc/mounts.
  # Spaces around the path so a prefix of another mount does not match.
  if ! grep -F " $STOCK_HOME/r2-share " /proc/mounts >/dev/null; then
    install -d -m 755 -o "$STOCK_USER" -g "$STOCK_USER" "$STOCK_HOME/r2-share"
  fi
  install -d -m 700 -o "$STOCK_USER" -g "$STOCK_USER" "$STOCK_HOME/.config"
  install -d -m 700 -o "$STOCK_USER" -g "$STOCK_USER" "$STOCK_HOME/.config/rclone"
  install -d -m 700 -o "$STOCK_USER" -g "$STOCK_USER" "$STOCK_HOME/.config/systemd"
  install -d -m 700 -o "$STOCK_USER" -g "$STOCK_USER" "$STOCK_HOME/.config/systemd/user"
  install -d -m 700 -o "$STOCK_USER" -g "$STOCK_USER" "$STOCK_HOME/.cache"
  install -d -m 700 -o "$STOCK_USER" -g "$STOCK_USER" "$STOCK_HOME/.cache/rclone"
  if command -v loginctl >/dev/null 2>&1; then
    loginctl enable-linger "$STOCK_USER" || echo "enable-linger ${STOCK_USER} failed" >&2
  fi
  if [[ -f "$APP_DIR/deploy/r2-share.service" ]]; then
    if ! command -v rclone >/dev/null 2>&1; then
      echo "rclone missing; skip r2-share.service"
    else
      # Restart only when unit/rclone.conf hashes change. Every-deploy restart
      # unmounts FUSE under running jobs (#549).
      # shellcheck source=r2-share-apply.sh
      # shellcheck disable=SC1091
      source "$APP_DIR/deploy/r2-share-apply.sh"
      r2_share_apply
    fi
  fi
fi

install -d -m 755 /etc/fluent-bit
install -d -m 755 /var/lib/fluent-bit
if [[ -f "$APP_DIR/deploy/fluent-bit.conf" ]]; then
  write_if_changed /etc/fluent-bit/fluent-bit.conf 644 < "$APP_DIR/deploy/fluent-bit.conf"
fi
if [[ -f "$APP_DIR/deploy/fluent-bit_slack.lua" ]]; then
  write_if_changed /etc/fluent-bit/slack.lua 644 < "$APP_DIR/deploy/fluent-bit_slack.lua"
fi
install -d -m 755 /etc/systemd/system/fluent-bit.service.d
write_if_changed /etc/systemd/system/fluent-bit.service.d/slack.conf 644 <<'EOF'
[Service]
EnvironmentFile=-/etc/fluent-bit/slack.env
EOF
slack_env_value() {
  local key="$1"
  local file="/etc/fluent-bit/slack.env"
  [[ -f "$file" ]] || return 0
  awk -v k="$key" 'index($0, k "=")==1 {print substr($0, length(k)+2); exit}' "$file"
}

slack_uri_from_url() {
  local url="$1"
  local label="${2:-SLACK_WEBHOOK_URL}"
  if [[ "$url" == *$'\n'* || "$url" == *$'\r'* ]]; then
    echo "${label} rejected (newline)" >&2
    return 1
  fi
  local rest="${url#https://}"
  local host="${rest%%/*}"
  local uri="/${rest#*/}"
  if [[ "$host" != "hooks.slack.com" ]]; then
    echo "${label} rejected (host)" >&2
    return 1
  fi
  if [[ "$uri" != /services/* ]]; then
    echo "${label} rejected (path)" >&2
    return 1
  fi
  printf '%s' "$uri"
}

slack_channel_uri() {
  local extra_url="$1"
  local label="$2"
  local default_uri="$3"
  if [[ -z "$extra_url" ]]; then
    printf '%s' "$default_uri"
    return 0
  fi
  local extra_uri
  extra_uri="$(slack_uri_from_url "$extra_url" "$label")" || extra_uri="$default_uri"
  printf '%s' "$extra_uri"
}

write_slack_env() {
  local url="$1"
  local uri
  uri="$(slack_uri_from_url "$url")" || return 1
  local app_uri batch_uri infra_uri quiet_uri
  app_uri="$(slack_channel_uri "${SLACK_WEBHOOK_URL_APP:-}" SLACK_WEBHOOK_URL_APP "$uri")"
  batch_uri="$(slack_channel_uri "${SLACK_WEBHOOK_URL_BATCH:-}" SLACK_WEBHOOK_URL_BATCH "$uri")"
  infra_uri="$(slack_channel_uri "${SLACK_WEBHOOK_URL_INFRA:-}" SLACK_WEBHOOK_URL_INFRA "$uri")"
  quiet_uri="$(slack_channel_uri "${SLACK_WEBHOOK_URL_QUIET:-}" SLACK_WEBHOOK_URL_QUIET "$infra_uri")"
  printf 'SLACK_WEBHOOK_URL=%s\nSLACK_WEBHOOK_URI=%s\nSLACK_WEBHOOK_URI_APP=%s\nSLACK_WEBHOOK_URI_BATCH=%s\nSLACK_WEBHOOK_URI_INFRA=%s\nSLACK_WEBHOOK_URI_QUIET=%s\n' \
    "$url" "$uri" "$app_uri" "$batch_uri" "$infra_uri" "$quiet_uri" | write_if_changed /etc/fluent-bit/slack.env 600
  chmod 600 /etc/fluent-bit/slack.env
}

slack_restore_channel_url() {
  local env_name="$1"
  local uri_key="$2"
  if [[ -n "${!env_name:-}" ]]; then
    return 0
  fi
  local existing_uri
  existing_uri="$(slack_env_value "$uri_key")"
  if [[ -n "$existing_uri" ]]; then
    printf -v "$env_name" '%s' "https://hooks.slack.com${existing_uri}"
  fi
}

if [[ -n "${SLACK_WEBHOOK_URL:-}" ]]; then
  write_slack_env "$SLACK_WEBHOOK_URL" || true
elif [[ -f /etc/fluent-bit/slack.env ]]; then
  existing_url="$(slack_env_value SLACK_WEBHOOK_URL)"
  slack_restore_channel_url SLACK_WEBHOOK_URL_APP SLACK_WEBHOOK_URI_APP
  slack_restore_channel_url SLACK_WEBHOOK_URL_BATCH SLACK_WEBHOOK_URI_BATCH
  slack_restore_channel_url SLACK_WEBHOOK_URL_INFRA SLACK_WEBHOOK_URI_INFRA
  slack_restore_channel_url SLACK_WEBHOOK_URL_QUIET SLACK_WEBHOOK_URI_QUIET
  if [[ -n "$existing_url" ]]; then
    write_slack_env "$existing_url" || true
  fi
fi
systemctl daemon-reload
if command -v fluent-bit >/dev/null 2>&1 || [[ -x /opt/fluent-bit/bin/fluent-bit ]]; then
  systemctl enable fluent-bit >/dev/null
  if [[ -f /etc/fluent-bit/slack.env ]] && grep -qE '^SLACK_WEBHOOK_URI=/services/.+' /etc/fluent-bit/slack.env; then
    systemctl restart fluent-bit
  else
    echo "fluent-bit enabled but not started (slack.env missing; set SLACK_WEBHOOK_URL and redeploy)"
  fi
else
  echo "fluent-bit missing; skip enable"
fi

echo "provision ok domain=${DOMAIN} tz=$(timedatectl show -p Timezone --value 2>/dev/null || echo Asia/Tokyo)"
