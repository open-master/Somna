#!/bin/sh
set -e
export TLS_DOMAIN="${TLS_DOMAIN:-somna-ai.com}"
LE="/etc/letsencrypt/live/$TLS_DOMAIN"

rm -f /etc/nginx/conf.d/*.conf

if [ -f "$LE/fullchain.pem" ] && [ -f "$LE/privkey.pem" ]; then
  cp /etc/nginx/templates/http-redirect.conf /etc/nginx/conf.d/00-http.conf
  envsubst '${TLS_DOMAIN}' < /etc/nginx/templates/https.conf.template > /etc/nginx/conf.d/01-https.conf
else
  cp /etc/nginx/templates/http-proxy.conf /etc/nginx/conf.d/00-http.conf
fi

exec /docker-entrypoint.sh "$@"
