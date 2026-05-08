#!/bin/sh
set -e
export TLS_DOMAIN="${TLS_DOMAIN:-somna-ai.com}"
LE="/etc/letsencrypt/live/$TLS_DOMAIN"

rm -f /etc/nginx/conf.d/*.conf

TPL=/etc/nginx/somna-templates
if [ -f "$LE/fullchain.pem" ] && [ -f "$LE/privkey.pem" ]; then
  cp "$TPL/http-redirect.conf" /etc/nginx/conf.d/00-http.conf
  envsubst '${TLS_DOMAIN}' < "$TPL/https.conf.template" > /etc/nginx/conf.d/01-https.conf
else
  cp "$TPL/http-proxy.conf" /etc/nginx/conf.d/00-http.conf
fi

exec /docker-entrypoint.sh "$@"
