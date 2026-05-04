#!/bin/sh
# Auto-create .htpasswd from environment variables
# This script runs via /docker-entrypoint.d/ in nginx:alpine image

MTX_USER="${MTX_UI_USER:-admin}"
MTX_PASS="${MTX_UI_PASSWORD:-changeme}"

if ! command -v htpasswd >/dev/null 2>&1 && command -v apk >/dev/null 2>&1; then
    apk add --no-cache apache2-utils >/dev/null 2>&1 || true
fi

if command -v htpasswd >/dev/null 2>&1; then
    htpasswd -bc /etc/nginx/.htpasswd "$MTX_USER" "$MTX_PASS"
    echo "[mtx-toolkit] Created .htpasswd for user: $MTX_USER"
else
    # Fallback: openssl apr1 hash
    HASH=$(openssl passwd -apr1 "$MTX_PASS" 2>/dev/null)
    if [ -n "$HASH" ]; then
        printf '%s:%s\n' "$MTX_USER" "$HASH" > /etc/nginx/.htpasswd
        echo "[mtx-toolkit] Created .htpasswd (openssl) for user: $MTX_USER"
    fi
fi

if [ ! -s /etc/nginx/.htpasswd ]; then
    echo "[mtx-toolkit] WARN: failed to create /etc/nginx/.htpasswd — disabling auth_basic." >&2
    sed -i '/auth_basic/d' /etc/nginx/conf.d/default.conf 2>/dev/null || true
fi
