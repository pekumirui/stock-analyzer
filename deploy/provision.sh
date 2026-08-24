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
  ufw tzdata

timedatectl set-timezone Asia/Tokyo

install -d -m 755 "$ACME_ROOT"
usermod -aG systemd-journal,adm "$STOCK_USER" 2>/dev/null || true

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
    ssl_session_timeout 1d;
    ssl_session_cache shared:SSL:10m;

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
    ssl_session_timeout 1d;
    ssl_session_cache shared:SSL:10m;

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
  certbot certonly --webroot -w "$ACME_ROOT" --expand --cert-name "$DOMAIN" \
    "${cert_names[@]}" "${certbot_opts[@]}"
  write_if_changed /etc/nginx/sites-available/stock-analyzer 644 < <(render_https) || true
  nginx -t
  systemctl reload nginx
fi

crontab -u "$STOCK_USER" "$APP_DIR/deploy/crontab"

echo "provision ok domain=${DOMAIN} tz=$(timedatectl show -p Timezone --value 2>/dev/null || echo Asia/Tokyo)"
