#!/bin/sh
set -eu

PUBLIC_HOST="${PUBLIC_HOST:-_}"
TLS_CERT_DIR="${TLS_CERT_DIR:-/etc/nginx/certs}"
TLS_FULLCHAIN_PATH="${TLS_FULLCHAIN_PATH:-${TLS_CERT_DIR}/fullchain.pem}"
TLS_PRIVKEY_PATH="${TLS_PRIVKEY_PATH:-${TLS_CERT_DIR}/privkey.pem}"
ACTIVE_CONF="/etc/nginx/conf.d/default.conf"

render_http_only() {
cat > "$ACTIVE_CONF" <<EOF
server {
    listen 80;
    server_name ${PUBLIC_HOST};

    location / {
        proxy_pass http://nvr-admin-ui:80;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host \$host;
        proxy_read_timeout 3600s;
    }

    location /ws/ {
        proxy_pass http://fleet-server:8765;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host \$host;
        proxy_read_timeout 86400s;
    }

    location /api/ {
        proxy_pass http://fleet-server:8765;
        proxy_set_header Host \$host;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }

    location /install.sh {
        proxy_pass http://fleet-server:8765;
        proxy_set_header Host \$host;
    }

    location /agent/ {
        proxy_pass http://fleet-server:8765;
        proxy_set_header Host \$host;
    }

    location /monitor/ {
        proxy_pass http://host.docker.internal:3001/;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host \$host;
    }

    location /hls/ {
        proxy_pass http://host.docker.internal:8888/;
        add_header Cache-Control no-cache;
        add_header Access-Control-Allow-Origin *;
    }

    location /webrtc/ {
        proxy_pass http://host.docker.internal:8889/;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
EOF
}

render_https() {
cat > "$ACTIVE_CONF" <<EOF
server {
    listen 80;
    server_name ${PUBLIC_HOST};
    return 301 https://\$host\$request_uri;
}

server {
    listen 443 ssl http2;
    server_name ${PUBLIC_HOST};

    ssl_certificate     ${TLS_FULLCHAIN_PATH};
    ssl_certificate_key ${TLS_PRIVKEY_PATH};
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_ciphers         HIGH:!aNULL:!MD5;

    location / {
        proxy_pass http://nvr-admin-ui:80;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host \$host;
        proxy_read_timeout 3600s;
    }

    location /ws/ {
        proxy_pass http://fleet-server:8765;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host \$host;
        proxy_read_timeout 86400s;
    }

    location /api/ {
        proxy_pass http://fleet-server:8765;
        proxy_set_header Host \$host;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }

    location /install.sh {
        proxy_pass http://fleet-server:8765;
        proxy_set_header Host \$host;
    }

    location /agent/ {
        proxy_pass http://fleet-server:8765;
        proxy_set_header Host \$host;
    }

    location /monitor/ {
        proxy_pass http://host.docker.internal:3001/;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host \$host;
    }

    location /hls/ {
        proxy_pass http://host.docker.internal:8888/;
        add_header Cache-Control no-cache;
        add_header Access-Control-Allow-Origin *;
    }

    location /webrtc/ {
        proxy_pass http://host.docker.internal:8889/;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
EOF
}

activate_best_config() {
    if [ -s "$TLS_FULLCHAIN_PATH" ] && [ -s "$TLS_PRIVKEY_PATH" ]; then
        render_https
        if nginx -t >/dev/null 2>&1; then
            echo "nginx: HTTPS configuration active"
            return 0
        fi
        echo "nginx: invalid TLS material, falling back to HTTP-only"
    fi
    render_http_only
    nginx -t >/dev/null 2>&1
    echo "nginx: HTTP-only configuration active"
}

watch_tls_changes() {
    last_state=""
    while true; do
        if [ -s "$TLS_FULLCHAIN_PATH" ] && [ -s "$TLS_PRIVKEY_PATH" ]; then
            state="$(sha256sum "$TLS_FULLCHAIN_PATH" "$TLS_PRIVKEY_PATH" 2>/dev/null | sha256sum | awk '{print $1}')"
        else
            state="http-only"
        fi
        if [ "$state" != "$last_state" ]; then
            if activate_best_config; then
                if [ -f /var/run/nginx.pid ]; then
                    nginx -s reload >/dev/null 2>&1 || true
                fi
                last_state="$state"
            fi
        fi
        sleep 5
    done
}

mkdir -p "$TLS_CERT_DIR"
activate_best_config
watch_tls_changes &
exec nginx -g 'daemon off;'
