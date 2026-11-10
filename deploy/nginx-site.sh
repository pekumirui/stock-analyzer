# shellcheck shell=bash

ACCESS_LOG="${ACCESS_LOG:-/var/log/nginx/stock-analyzer.access.log}"
APP_DIR="${APP_DIR:-/home/stock/stock-analyzer}"
APP_UPSTREAM="${APP_UPSTREAM:-127.0.0.1:8000}"
MCP_UPSTREAM="${MCP_UPSTREAM:-127.0.0.1:8010}"

auth_block=$(cat <<EOF
    auth_request /_cf_access;
EOF
)

CF_IPS="173.245.48.0/20 103.21.244.0/22 103.22.200.0/22 103.31.4.0/22 141.101.64.0/18 108.162.192.0/18 190.93.240.0/20 188.114.96.0/20 197.234.240.0/22 198.41.128.0/17 162.158.0.0/15 104.16.0.0/13 104.24.0.0/14 172.64.0.0/13 131.0.72.0/22 2400:cb00::/32 2606:4700::/32 2803:f800::/32 2405:b500::/32 2405:8100::/32 2a06:98c0::/29 2c0f:f248::/32"

render_realip_conf() {
  local ip
  for ip in $CF_IPS; do
    printf 'set_real_ip_from %s;\n' "$ip"
  done
  printf 'real_ip_header CF-Connecting-IP;\n'
}

render_limit_conf() {
  cat <<'EOF'
limit_conn_zone $binary_remote_addr zone=addr:10m;
EOF
}

render_log_conf() {
  cat <<'EOF'
log_format timed '$remote_addr [$time_local] "$request" $status $body_bytes_sent '
                 'rt=$request_time urt=$upstream_response_time "$http_user_agent"';
EOF
}

render_gzip_conf() {
  cat <<'EOF'
gzip_vary on;
gzip_types text/css application/javascript text/javascript application/json text/plain application/xml text/xml;
EOF
}

pub_headers=$(cat <<'EOF'
        add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
        add_header X-Content-Type-Options "nosniff" always;
        add_header Referrer-Policy "strict-origin-when-cross-origin" always;
        add_header X-Frame-Options "DENY" always;
        add_header Permissions-Policy "camera=(), microphone=(), geolocation=()" always;
EOF
)

proxy_block=$(cat <<EOF
    client_max_body_size 1m;
    location = /_cf_access {
        internal;
        proxy_pass http://${APP_UPSTREAM}/_auth/cf-access;
        proxy_pass_request_body off;
        proxy_set_header Content-Length "";
        proxy_set_header Host \$host;
        proxy_read_timeout 10s;
    }
    location = / {
        return 301 /monthly;
    }
    location = /robots.txt {
        limit_conn addr 16;
${pub_headers}
        proxy_pass http://${APP_UPSTREAM};
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 60s;
    }
    location = /sitemap.xml {
        limit_conn addr 16;
${pub_headers}
        proxy_pass http://${APP_UPSTREAM};
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 60s;
    }
    location = /favicon.ico {
${pub_headers}
        proxy_pass http://${APP_UPSTREAM};
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 60s;
    }
    location ^~ /monthly {
        limit_conn addr 16;
        limit_except GET { deny all; }
${pub_headers}
        add_header Content-Security-Policy "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'" always;
        proxy_pass http://${APP_UPSTREAM};
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 60s;
    }
    location = /static/monthly_row_order.json {
${pub_headers}
        proxy_pass http://${APP_UPSTREAM};
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 60s;
    }
    location = /static/ir_monthly_codes.json {
${pub_headers}
        proxy_pass http://${APP_UPSTREAM};
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 60s;
    }
    location ^~ /static/ {
${pub_headers}
        add_header Cache-Control "no-cache" always;
        alias ${APP_DIR}/static/;
    }
    location = /api/v1/tables {
        limit_conn addr 16;
        limit_except GET { deny all; }
${pub_headers}
        proxy_pass http://${APP_UPSTREAM};
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 60s;
    }
    location ~ "^/api/v1/tables/[0-9][0-9A-Z]{3}[0-9](?:/[0-9]{4})?\$" {
        limit_conn addr 16;
        limit_except GET { deny all; }
${pub_headers}
        proxy_pass http://${APP_UPSTREAM};
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 60s;
    }
    location ^~ /app {
${auth_block}
        proxy_pass http://${APP_UPSTREAM};
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 60s;
    }
    location ^~ /docs {
${auth_block}
        proxy_pass http://${APP_UPSTREAM};
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 60s;
    }
    location ^~ /redoc {
${auth_block}
        proxy_pass http://${APP_UPSTREAM};
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 60s;
    }
    location = /openapi.json {
${auth_block}
        proxy_pass http://${APP_UPSTREAM};
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 60s;
    }
    location ^~ /.well-known/oauth- {
        proxy_pass http://${MCP_UPSTREAM};
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_set_header Authorization \$http_authorization;
        proxy_set_header Content-Type \$content_type;
        client_max_body_size 1m;
    }
    location ^~ /.well-known/openid-configuration {
        proxy_pass http://${MCP_UPSTREAM};
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
        proxy_pass http://${MCP_UPSTREAM};
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
        proxy_pass http://${MCP_UPSTREAM};
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
        proxy_pass http://${APP_UPSTREAM};
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
    access_log ${ACCESS_LOG} timed;

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
    access_log ${ACCESS_LOG} timed;

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

