#!/bin/sh
set -e

# Auto-create .htpasswd if credentials are set
MTX_USER="${MTX_UI_USER:-admin}"
MTX_PASS="${MTX_UI_PASSWORD:-changeme}"

if command -v htpasswd >/dev/null 2>&1; then
    htpasswd -bc /etc/nginx/.htpasswd "$MTX_USER" "$MTX_PASS"
    echo "Created .htpasswd for user: $MTX_USER"
else
    # Fallback: create htpasswd manually using openssl
    HASH=$(openssl passwd -apr1 "$MTX_PASS")
    echo "$MTX_USER:$HASH" > /etc/nginx/.htpasswd
    echo "Created .htpasswd (openssl) for user: $MTX_USER"
fi

# Start nginx
exec nginx -g "daemon off;"
