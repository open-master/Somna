# Somna AI — Runbook（运维手册）

## 1. 环境要求

| 组件 | 版本 |
|---|---|
| Docker | ≥ 24 |
| Docker Compose | v2（`docker compose` 命令） |
| 可用内存 | ≥ 8 GB（推荐 16 GB） |
| 磁盘 | ≥ 40 GB |
| OS | macOS / Linux（WSL2 亦可） |

## 2. 端口占用表

| 服务 | 宿主端口 | 说明 |
|---|---|---|
| Web (前端) | 3000 | Next.js |
| BFF | 8080 | Node.js Hono |
| Agent Core | 8000 | FastAPI |
| MCP Hub | （无宿主端口） | FastAPI，仅 Docker 内网 `mcp-hub:8090`；调用需 `MCP_HUB_INTERNAL_TOKEN` |
| LiteLLM | 4000 | LLM 网关 |
| Temporal gRPC | 7233 | |
| Temporal UI | 8233 | |
| Langfuse | 3001 | |
| Postgres | 5432 | |
| MySQL | 默认 3306，可用 `MYSQL_HOST_PORT` 改 | 云主机常与系统 MySQL 冲突，见 `.env` |
| Milvus gRPC | 19530 | |
| Milvus HTTP | 9091 | 健康检查 |
| Redis | 6379 | |
| NATS client | 4222 | |
| NATS monitor | 8222 | |
| MinIO API | 9000 | |
| MinIO Console | 9001 | |

生产叠加 `docker-compose.nginx.yml` 时，上表除 Nginx **80/443** 外均不映射宿主；健康检查用 `docker compose exec`（见 `scripts/health_check.sh`）。

## 3. 常用命令

```bash
make bootstrap   # 首次：生成 .env、创建网络、拉镜像
make up          # 启动全栈
make ps          # 服务状态
make health      # 健康检查（脚本）
make logs                     # 跟随所有日志
make logs s=agent-core        # 跟随某个服务
make restart                  # 重启全栈
make down                     # 停止（保留数据）
make clean                    # 清容器（保留卷）
make reset                    # ⚠️ 清数据卷（不可逆）
```

## 4. 首次启动步骤

```bash
# 1. 克隆 & 进入
cd SomnaAI

# 2. 初始化
make bootstrap

# 3. 编辑 .env，至少填：
# MOONSHOT_API_KEY / DASHSCOPE_API_KEY / DEEPSEEK_API_KEY / SILICONFLOW_API_KEY
vim .env

# 4. 启动
make up

# 5. 等 2-3 分钟后健康检查
make health
```

## 5. 验证 LiteLLM 能通到模型

```bash
# OpenAI 兼容
curl -sS http://localhost:4000/v1/chat/completions \
  -H "Authorization: Bearer $(grep LITELLM_MASTER_KEY .env | cut -d= -f2)" \
  -H "Content-Type: application/json" \
  -d '{"model": "agent-executor", "messages":[{"role":"user","content":"你好"}]}' | jq

# Anthropic 兼容（Claude SDK 也是走这个）
curl -sS http://localhost:4000/anthropic/v1/messages \
  -H "x-api-key: $(grep LITELLM_MASTER_KEY .env | cut -d= -f2)" \
  -H "anthropic-version: 2023-06-01" \
  -H "Content-Type: application/json" \
  -d '{"model": "agent-executor", "max_tokens": 200,
       "messages":[{"role":"user","content":"hi"}]}' | jq

# Embedding
curl -sS http://localhost:4000/v1/embeddings \
  -H "Authorization: Bearer $(grep LITELLM_MASTER_KEY .env | cut -d= -f2)" \
  -H "Content-Type: application/json" \
  -d '{"model": "agent-embed", "input": "测试文本"}' | jq '.data[0].embedding | length'
```

## 6. Langfuse 首次登录

1. 打开 http://localhost:3001
2. 注册（首次任意邮箱会成为管理员）
3. 新建 Project → 拿到 `public_key` 和 `secret_key`
4. 写入 `.env` 的 `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY`
5. `make restart` 让 LiteLLM 重新读取

## 7. MinIO 首次配置

- Console: http://localhost:9001，账号 = `.env` 中 `S3_ACCESS_KEY` / `S3_SECRET_KEY`
- `minio-init` 服务会自动创建 `somna` 和 `langfuse` 两个 bucket
- 需要公网访问时，把 `NEXT_PUBLIC_API_BASE` 改成实际域名

## 8. Temporal

- UI: http://localhost:8233
- 命令行：`docker compose exec temporal tctl namespace list`

## 9. Postgres / MySQL 连接

```bash
# Postgres
docker compose exec postgres psql -U somna -d somna
\dt
\q

# MySQL
docker compose exec mysql mysql -usomna -p somna_biz
SHOW TABLES;
\q
```

## 10. 常见故障排查

### 10.1 LiteLLM 报 429 / 401
- 检查对应厂商 API Key 是否填对、是否有余额
- 检查 `drop_params: true` 是否开启（国产模型会因多余字段失败）
- 进入容器：`docker compose exec litellm tail -f /app/logs/error.log`

### 10.2 Milvus 起不来
- 多等 2 分钟，首次启动初始化慢
- 查 etcd：`docker compose logs milvus-etcd`
- `make reset` 可强行清空（丢数据）

### 10.3 Temporal 报 schema 错误
- `auto-setup` 镜像会自动建表，失败通常是 Postgres 没 ready
- 先 `make down` 再 `make up`，顺序会按 depends_on 执行

### 10.4 Langfuse 打不开
- 等 `langfuse-db` 起来，首次约 30s
- 检查 `.env` 里 `LANGFUSE_NEXTAUTH_SECRET` 和 `LANGFUSE_SALT` 必须填

### 10.5 Docker 空间爆了
```bash
docker system prune -a --volumes  # ⚠️ 会删所有无引用卷
```

## 11. 升级 & 数据迁移

- 配置改动：改 `.env` 或 `infra/litellm/config.yaml` → `make restart`
- 数据库 schema 变更：新增 `infra/postgres/init/02_xxx.sql`，在容器里手动 `psql -f` 执行（`docker-entrypoint-initdb.d` 只在首次初始化时跑）
- LiteLLM model 增删：改 `infra/litellm/config.yaml` → `docker compose restart litellm`

## 12. 生产化 TODO（超出 M2 范围）

- [ ] 改 `.env` 为 SOPS 加密或 Vault
- [ ] 前端 / BFF / Agent Core 加 HTTPS 反代（nginx/caddy）
- [ ] 沙盒迁 K8s + Firecracker
- [ ] 引入 Argo CD 做 GitOps 部署
- [ ] 多副本 + Postgres 主备
