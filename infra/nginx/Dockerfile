# syntax=docker/dockerfile:1.7
FROM nginx:1.27-alpine
RUN apk add --no-cache gettext
COPY nginx.conf /etc/nginx/nginx.conf
COPY snippets /etc/nginx/snippets
COPY templates /etc/nginx/templates
COPY docker-entrypoint.sh /docker-entrypoint-letsencrypt.sh
RUN chmod +x /docker-entrypoint-letsencrypt.sh
ENTRYPOINT ["/docker-entrypoint-letsencrypt.sh"]
CMD ["nginx", "-g", "daemon off;"]
