# syntax=docker/dockerfile:1.7
FROM nginx:1.27-alpine
RUN apk add --no-cache gettext
COPY nginx.conf /etc/nginx/nginx.conf
COPY snippets /etc/nginx/snippets
# 勿放在 /etc/nginx/templates：官方 docker-entrypoint 会对其中的 *.template 做 envsubst
# 并写入 conf.d，导致尚未签发证书时仍生成 HTTPS 片段、nginx 因缺证书无限重启。
COPY templates /etc/nginx/somna-templates
COPY docker-entrypoint.sh /docker-entrypoint-letsencrypt.sh
RUN chmod +x /docker-entrypoint-letsencrypt.sh
ENTRYPOINT ["/docker-entrypoint-letsencrypt.sh"]
CMD ["nginx", "-g", "daemon off;"]
