# shellcheck shell=bash

ACCESS_LOG="${ACCESS_LOG:-/var/log/nginx/stock-analyzer.access.log}"
APP_DIR="${APP_DIR:-/home/stock/stock-analyzer}"
APP_UPSTREAM="${APP_UPSTREAM:-127.0.0.1:8000}"
MCP_UPSTREAM="${MCP_UPSTREAM:-127.0.0.1:8010}"

auth_block=""
if [[ -f "$HTPASSWD" ]]; then
  auth_block=$(cat <<EOF
    auth_basic "stock-analyzer";
    auth_basic_user_file ${HTPASSWD};
EOF
)
fi

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
gzip on;
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
    location = / {
        auth_basic off;
        return 301 /monthly;
    }
    location = /robots.txt {
        auth_basic off;
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
        auth_basic off;
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
        auth_basic off;
${pub_headers}
        proxy_pass http://${APP_UPSTREAM};
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 60s;
    }
    location ^~ /monthly {
        auth_basic off;
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
        auth_basic off;
${pub_headers}
        proxy_pass http://${APP_UPSTREAM};
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 60s;
    }
    location = /static/ir_monthly_codes.json {
        auth_basic off;
${pub_headers}
        proxy_pass http://${APP_UPSTREAM};
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 60s;
    }
    location ^~ /static/ {
        auth_basic off;
${pub_headers}
        add_header Cache-Control "no-cache" always;
        alias ${APP_DIR}/static/;
    }
    location = /api/v1/tables {
        auth_basic off;
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
        auth_basic off;
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
        auth_basic off;
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
        auth_basic off;
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
        auth_basic off;
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
        auth_basic off;
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

