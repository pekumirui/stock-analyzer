#!/usr/bin/env bash
# root 用。何度実行しても同じ状態になる。
# GitHub Actions → update.sh → sudo deploy/provision.sh
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "root で実行してください（sudo deploy/provision.sh）" >&2
  exit 1
fi

APP_DIR="${APP_DIR:-/home/stock/stock-analyzer}"
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

apt-get update -qq
apt-get install -y -qq \
  python3-venv python3-pip git curl \
  nginx apache2-utils certbot \
  ufw tzdata fuse3 unzip

timedatectl set-timezone Asia/Tokyo

# rclone: Debian 版は古いことがある。無ければ公式インストーラ。
if ! command -v rclone >/dev/null 2>&1; then
  curl -fsSL https://rclone.org/install.sh | bash || echo "rclone install failed (R2 mount skipped)" >&2
else
  echo "rclone present: $(rclone version 2>/dev/null | head -n1)"
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
if [[ -s "$APP_DIR/batch.log" && ! -s "$LOG_DIR/batch.log" ]]; then
  cp "$APP_DIR/batch.log" "$LOG_DIR/batch.log"
  chown "$STOCK_USER:$STOCK_USER" "$LOG_DIR/batch.log"
  echo "migrated batch.log → $LOG_DIR"
fi
if [[ -f "$APP_DIR/deploy/logrotate" ]]; then
  write_if_changed /etc/logrotate.d/stock-analyzer 644 < "$APP_DIR/deploy/logrotate"
fi

if write_if_changed /etc/systemd/system/stock-analyzer.service 644 < "$APP_DIR/deploy/stock-analyzer.service"; then
  systemctl daemon-reload
fi
systemctl enable stock-analyzer >/dev/null

if write_if_changed /etc/systemd/system/stock-analyzer-mcp.service 644 < "$APP_DIR/deploy/stock-analyzer-mcp.service"; then
  systemctl daemon-reload
fi
systemctl enable stock-analyzer-mcp >/dev/null

if [[ -f "$APP_DIR/deploy/sudoers" ]]; then
  if visudo -cf "$APP_DIR/deploy/sudoers" >/dev/null; then
    write_if_changed /etc/sudoers.d/stock-analyzer 440 < "$APP_DIR/deploy/sudoers" || true
  else
    echo "sudoers の visudo 検証に失敗したので更新しません" >&2
    exit 1
  fi
fi

if [[ -n "${BASIC_AUTH_USER:-}" && -n "${BASIC_AUTH_PASSWORD:-}" ]]; then
  touch "$HTPASSWD"
  htpasswd -bB "$HTPASSWD" "$BASIC_AUTH_USER" "$BASIC_AUTH_PASSWORD" >/dev/null
  chmod 640 "$HTPASSWD"
  chown root:www-data "$HTPASSWD"
  echo "htpasswd ok (${BASIC_AUTH_USER})"
fi

auth_block=""
if [[ -f "$HTPASSWD" ]]; then
  auth_block=$(cat <<'EOF'
    auth_basic "stock-analyzer";
    auth_basic_user_file /etc/nginx/stock-analyzer.htpasswd;
EOF
)
fi

proxy_block=$(cat <<EOF
    client_max_body_size 1m;
    location ^~ /.well-known/oauth- {
        auth_basic off;
        proxy_pass http://127.0.0.1:8010;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_set_header Authorization \$http_authorization;
        proxy_set_header Content-Type \$content_type;
        client_max_body_size 1m;
    }
    location = /.well-known/openid-configuration {
        auth_basic off;
        proxy_pass http://127.0.0.1:8010;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_set_header Authorization \$http_authorization;
        proxy_set_header Content-Type \$content_type;
        client_max_body_size 1m;
    }
    location ^~ /oauth/ {
        auth_basic off;
        proxy_pass http://127.0.0.1:8010;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_set_header Authorization \$http_authorization;
        proxy_set_header Content-Type \$content_type;
        proxy_set_header Accept \$http_accept;
        client_max_body_size 1m;
    }
    location ~ ^/mcp/?$ {
        auth_basic off;
        proxy_pass http://127.0.0.1:8010;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_set_header Authorization \$http_authorization;
        proxy_set_header X-MCP-Token \$http_x_mcp_token;
        proxy_set_header Accept \$http_accept;
        proxy_set_header Content-Type \$content_type;
        proxy_set_header Mcp-Session-Id \$http_mcp_session_id;
        proxy_set_header Connection "";
        proxy_buffering off;
        proxy_request_buffering off;
        proxy_cache off;
        proxy_read_timeout 300s;
        proxy_send_timeout 300s;
        client_max_body_size 1m;
    }
    location / {
${auth_block}
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 60s;
    }
EOF
)

acme_block=$(cat <<EOF
    location /.well-known/acme-challenge/ {
        auth_basic off;
        allow all;
        root ${ACME_ROOT};
    }
EOF
)

render_http_only() {
  cat <<EOF
server {
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name ${SERVER_NAMES};

    ${acme_block}

${proxy_block}
}
EOF
}

render_https() {
  cat <<EOF
server {
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name ${SERVER_NAMES};

    ${acme_block}

    location / {
        return 301 https://${DOMAIN}\$request_uri;
    }
}
EOF
  if [[ -n "$WWW" ]]; then
    cat <<EOF

server {
    listen 443 ssl;
    listen [::]:443 ssl;
    server_name ${WWW};

    ssl_certificate ${CERT_LIVE}/fullchain.pem;
    ssl_certificate_key ${CERT_LIVE}/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_session_timeout 1d;
    ssl_session_cache shared:SSL:10m;
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;

    return 301 https://${DOMAIN}\$request_uri;
}
EOF
  fi
  cat <<EOF

server {
    listen 443 ssl;
    listen [::]:443 ssl;
    server_name ${DOMAIN} _;

    ssl_certificate ${CERT_LIVE}/fullchain.pem;
    ssl_certificate_key ${CERT_LIVE}/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_session_timeout 1d;
    ssl_session_cache shared:SSL:10m;
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;

${proxy_block}
}
EOF
}

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

crontab -u "$STOCK_USER" "$APP_DIR/deploy/crontab"

# Cloudflare R2: stock の user systemd で rclone mount（linger で再起動後も残す）
STOCK_HOME="$(getent passwd "$STOCK_USER" | cut -d: -f6)"
if [[ -z "$STOCK_HOME" ]]; then
  echo "stock home not found; skip R2 mount" >&2
else
  # rclone FUSE rejects chown on a live mountpoint; skip if already mounted
  if ! mountpoint -q "$STOCK_HOME/r2-share"; then
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
      write_if_changed "$STOCK_HOME/.config/systemd/user/r2-share.service" 644 < "$APP_DIR/deploy/r2-share.service" || true
      chown "$STOCK_USER:$STOCK_USER" "$STOCK_HOME/.config/systemd/user/r2-share.service"
      user_sys_ok=0
      for _ in $(seq 1 15); do
        if systemctl --user -M "${STOCK_USER}@" daemon-reload >/dev/null 2>&1; then
          user_sys_ok=1
          break
        fi
        sleep 1
      done
      if [[ "$user_sys_ok" -eq 1 ]]; then
        systemctl --user -M "${STOCK_USER}@" enable r2-share.service >/dev/null 2>&1 || true
        if [[ -f "$STOCK_HOME/.config/rclone/rclone.conf" ]]; then
          systemctl --user -M "${STOCK_USER}@" restart r2-share.service >/dev/null 2>&1 \
            || echo "r2-share.service start skipped or failed (conf present)" >&2
        else
          echo "r2-share.service enabled but not started (rclone.conf missing; set R2_* secrets and redeploy)"
        fi
      else
        echo "user systemd for ${STOCK_USER} not ready; r2-share.service not enabled" >&2
      fi
    fi
  fi
fi

echo "provision ok domain=${DOMAIN} tz=$(timedatectl show -p Timezone --value 2>/dev/null || echo Asia/Tokyo)"
