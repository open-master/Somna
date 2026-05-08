# 生产部署：Docker Compose + Nginx 反代 + HTTPS

仓库提供可选叠加编排 **`docker-compose.nginx.yml`**：在 **`docker-compose.yml`** 全套服务之上增加 **Nginx**（端口 **80 / 443**），将公网流量反代到内部 **`web:3000`**（Next.js），并使用 **Let's Encrypt**（certbot webroot）签发证书。

- **域名示例**：`somna-ai.com`、`www.somna-ai.com`（模板中已写死 `server_name`；若改用其他域名，需同步修改 `infra/nginx/templates/` 下各 conf，并重新构建 nginx 镜像。）
- **证书目录名**：与 certbot **`certonly` 时 `-d` 的第一个域名** 一致（默认与 **`TLS_DOMAIN`** 一致）。

## 前置条件

1. **DNS**：将 `somna-ai.com`、`www.somna-ai.com` **A 记录** 指向云主机公网 IP。
2. **安全组 / 防火墙**：放行入站 **TCP 80**（HTTP 与 ACME 校验）、**TCP 443**（HTTPS）。签发前 Nginx 会在 80 上提供 `/.well-known/acme-challenge/`。
3. **环境变量**：在根目录 **`.env`** 中至少配置（示例见 **`.env.example`** 中的 Nginx/TLS 小节）：
   - **`TLS_DOMAIN`**：与证书 live 目录名一致，一般为首个 `-d` 域名，默认 `somna-ai.com`。
   - **`CERTBOT_EMAIL`**：Let's Encrypt 账户邮箱。
4. **`certbot` 服务使用 Compose profile**：`certbot` 配置了 **`profiles: [tls]`**，不会随普通 `up` 启动；执行签发/续期时必须加 **`--profile tls`**。

## 启动（含 Nginx）

在项目根目录执行：

```bash
docker compose -f docker-compose.yml -f docker-compose.nginx.yml up -d --build
```

效果简述：

- **`nginx`**：对外 **80 / 443**；读 **`certbot-conf`**（证书，只读）与 **`certbot-www`**（ACME webroot）。
- **`web`**：不再向宿主机暴露 **3000**（`ports` 被 `!override []`）；仅 **`expose: "3000"`**，由 Nginx 在 Compose 网络内访问。
- 若 **`/etc/letsencrypt/live/$TLS_DOMAIN/`** 下尚无有效证书，`docker-entrypoint` 会先启用 **仅 80**：ACME + 反代 Next；签发成功后重启 **nginx** 会切换为 **HTTP→HTTPS + 443**。

## 首次签发证书（webroot）

确保 Nginx 已启动且 80 可从公网访问，然后执行（注意 **`--profile tls`**）：

```bash
docker compose -f docker-compose.yml -f docker-compose.nginx.yml --profile tls run --rm certbot certonly \
  --webroot -w /var/www/certbot \
  -d somna-ai.com -d www.somna-ai.com \
  --email "${CERTBOT_EMAIL}" --agree-tos --no-eff-email
```

签发完成后让 Nginx 重新加载配置（会检测到证书并启用 443）：

```bash
docker compose -f docker-compose.yml -f docker-compose.nginx.yml restart nginx
```

若你修改了 **`-d` 首个域名**，请同步把 **`.env` 中的 `TLS_DOMAIN`** 设为该域名，与 `live/<域名>/` 目录一致。

## 续期

同样使用 **`--profile tls`**，例如由宿主机 **cron** 定期执行：

```bash
docker compose -f docker-compose.yml -f docker-compose.nginx.yml --profile tls run --rm certbot renew --webroot -w /var/www/certbot
docker compose -f docker-compose.yml -f docker-compose.nginx.yml exec nginx nginx -s reload
```

## 应用层环境变量（HTTPS）

- **`AUTH_URL`（Better Auth）**：生产应设为对外 **HTTPS 根地址**，例如 `https://somna-ai.com`（与浏览器地址栏一致），勿再使用 `http://localhost:3000`。
- **前端公开变量**：若浏览器需直连 **WebSocket** 等，请将 **`NEXT_PUBLIC_WS_BASE`** / **`NEXT_PUBLIC_API_BASE`** 等改为 **`wss://` / `https://`** 且指向公网可达的主机或路径；具体取决于你是否把 BFF/WS 也挂在同一 Nginx 主机名下（当前叠加文件仅反代 **`web:3000`**，API 多走 Next 同源 `/api` 时可少改公开变量）。

Nginx 模板已为代理请求设置 **`Upgrade` / `Connection`**（见 `infra/nginx/nginx.conf` 中的 `map`），便于 **WebSocket** 经反代到 Next。

## 校验 Compose 配置

```bash
docker compose -f docker-compose.yml -f docker-compose.nginx.yml config
```

无报错即 YAML 与合并结果有效。

## 相关文件

| 路径 | 说明 |
|------|------|
| `docker-compose.nginx.yml` | Nginx、web 端口覆盖、certbot 卷与 profile |
| `infra/nginx/` | 镜像构建、入口脚本、HTTP/HTTPS 模板、TLS 片段 |

更多通用运维见 **[`runbook.md`](runbook.md)**。
