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
  return 1
}

apt-get update -qq
apt-get install -y -qq \
  python3-venv python3-pip git curl \
  nginx apache2-utils certbot \
  ufw

install -d -m 755 "$ACME_ROOT"

if write_if_changed /etc/systemd/system/stock-analyzer.service 644 < "$APP_DIR/deploy/stock-analyzer.service"; then
  systemctl daemon-reload
fi
systemctl enable stock-analyzer >/dev/null

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
    server_name ${DOMAIN} _;

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
    server_name ${DOMAIN} _;

    ${acme_block}

    location / {
        return 301 https://\$host\$request_uri;
    }
}

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
  if [[ ! -f "${CERT_LIVE}/fullchain.pem" ]]; then
    echo "certbot: issue ${DOMAIN}"
    certbot certonly --webroot -w "$ACME_ROOT" -d "$DOMAIN" "${certbot_opts[@]}"
    write_if_changed /etc/nginx/sites-available/stock-analyzer 644 < <(render_https) || true
    nginx -t
    systemctl reload nginx
  fi
fi

crontab -u "$STOCK_USER" "$APP_DIR/deploy/crontab"

echo "provision ok domain=${DOMAIN}"
