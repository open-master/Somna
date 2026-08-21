# Somna AI

**对标 Manus 的通用 Agent 平台** — LangGraph 会话状态机、Claude Agent SDK 执行循环、LiteLLM 统一网关（DeepSeek / Kimi / Qwen / 硅基流动等），配合 MCP 沙盒与分层记忆。

开源仓库：[github.com/open-master/Somna](https://github.com/open-master/Somna)

---

## 简介

> 长任务不断线、工具可观测、模型可切换：Somna AI 面向「会话级 Agent + 独立沙盒 + 分层记忆」的一体化研发与部署骨架。

Somna 将 **Temporal** 用于可恢复编排，**Agent Core** 承载 LangGraph 图与 Claude SDK 双模式执行，**MCP Hub** 聚合搜索与工具，**Next.js** 前端提供 Manus 风格三栏工作台。数据面覆盖 Postgres（状态与 pgvector）、MySQL、Milvus、Redis、MinIO 与 NATS，便于从单机 Docker 平滑演进到未来 K8s。

## 功能特性

### 会话与编排

- **Temporal 工作流**：长任务、断线续跑、人工介入与超时控制（可扩展）
- **LangGraph 状态机**：ingest → task_frame → plan → execute ↔ reflect → finalize（压缩在 execute 内）
- **双执行模式**：自研 OpenAI 兼容 loop；**模式二** 走 Claude Agent SDK + MCP

### 沙盒与工具

- **每会话 Docker 沙盒**：浏览器、终端、文件、代码执行（能力随沙盒镜像演进）
- **MCP Hub**：工具路由、搜索提供商可配置（Brave / Tavily / Serper 等）

### 记忆与数据

- **分层记忆（规划）**：mem0 + Milvus 向量、Postgres 状态、Redis 热缓存（按 `MEMORY_ENABLED` 等开关启用）
- **对象与向量**：MinIO（S3 兼容）、Milvus（独立 etcd + 内置 MinIO）

### 前端与可观测

- **Next.js + shadcn/ui**：会话列表 / 对话流 / Live Computer（Manus 对齐目标）
- **Langfuse + OpenTelemetry**：链路追踪与指标（随部署打开）

## 技术栈

| 层 | 选型 |
|---|---|
| 前端 | Next.js 14 · shadcn/ui · SSE + WebSocket（AgentEvent） |
| BFF | Node.js (Hono) |
| Agent Core | Python · FastAPI · LangGraph · Claude Agent SDK · LangChain · mem0 |
| 工具网关 | MCP Hub (FastAPI) |
| 编排 | Temporal |
| LLM 网关 | LiteLLM Proxy |
| 模型 | DeepSeek · Kimi K2 · Qwen3 · bge-m3（硅基流动）等 |
| 存储 | MySQL · Postgres (+ pgvector) · Milvus · Redis · MinIO |
| 事件 | NATS JetStream |
| 观测 | Langfuse · OpenTelemetry · Grafana + Loki + Tempo（可选） |
| 部署 | Docker Compose（单机）→ 未来 K8s |

## 快速开始

### 前置要求

- **Docker** & **Docker Compose**（推荐最新稳定版）
- **Make**（可选，用于 `make up` 等快捷命令）
- 各家 **LLM / Embedding API Key**（见 `.env.example` 说明）

### 克隆与配置

```bash
# HTTPS（通用）
git clone https://github.com/open-master/Somna.git

# SSH：本机为 open-master 账号配置了 Host github-master 时（见 ~/.ssh/config），可用：
# git clone git@github-master:open-master/Somna.git

cd Somna

# 推送/Pull 建议使用与克隆一致的地址；维护者将 origin 设为 git@github-master:open-master/Somna.git 可固定走 open-master 密钥

# 复制环境变量模板并按需填入密钥（切勿提交 .env）
cp .env.example .env
# 至少配置：DEEPSEEK_API_KEY / MOONSHOT_API_KEY / DASHSCOPE_API_KEY / SILICONFLOW_API_KEY 等
```

### 启动全栈

项目在仓库根目录读取 **`.env`**（与 `docker-compose.yml` 同级）。若尚未创建，先执行 `cp .env.example .env` 并填好密钥。

#### 方式一：Docker Compose（推荐 / 原生）

在 **项目根目录**（上一步 `cd Somna` 后的目录）执行：

```bash
# 后台启动全部服务（默认会合并 docker-compose.yml + docker-compose.override.yml）
docker compose up -d

# 查看状态 / 日志
docker compose ps
docker compose logs -f --tail=200          # 全部服务
docker compose logs -f --tail=200 agent-core   # 单个服务

# 重启与停止（保留命名卷中的数据）
docker compose restart
docker compose down

# 仅停止并删容器、不删卷（与 make clean 类似）
docker compose down --remove-orphans

# 更新镜像后重建并启动（示例）
docker compose pull
docker compose up -d

# 健康检查
bash scripts/health_check.sh
```

**仅使用基准编排、不加载 override**（例如需要把 Redis / LiteLLM 端口映射回宿主机时）：

```bash
docker compose -f docker-compose.yml up -d
docker compose -f docker-compose.yml down
```

> **说明：** 默认合并 **`docker-compose.override.yml`**（常见用途：关闭本机 `redis` / `litellm` 的宿主机端口映射，避免与已有进程冲突；容器网络内服务名访问不受影响）。若不需要，请使用上文 `-f docker-compose.yml` 单文件方式。

#### 生产：Nginx 反代 + HTTPS（可选）

叠加上 **`docker-compose.nginx.yml`** 可对 **80 / 443** 暴露 Nginx，内部反代 **`web:3000`**，并用 certbot（**`--profile tls`**）签发 Let's Encrypt。配置 **`TLS_DOMAIN`**、**`CERTBOT_EMAIL`** 及 **`AUTH_URL` 等** 说明见 **[`docs/deployment-nginx-https.md`](docs/deployment-nginx-https.md)**。

```bash
docker compose -f docker-compose.yml -f docker-compose.nginx.yml up -d --build
```

#### 方式二：Make（封装了上述 Compose 命令）

```bash
make bootstrap    # 可选：首次创建 .env / 网络 / 数据卷（见 scripts/bootstrap.sh）
make up           # 等价于 docker compose up -d（会先确保存在 .env）
make health       # bash scripts/health_check.sh
make down         # docker compose down
make ps           # docker compose ps
make logs         # docker compose logs -f；指定服务：make logs s=agent-core
make restart      # docker compose restart
```

其他目标见 `make help`（含 `clean`、`reset` 等）。

### 本地服务一览

| 服务 | 地址 |
|---|---|
| Web（前端） | http://localhost:3000 |
| BFF | http://localhost:8080 |
| Agent Core | http://localhost:8000 |
| MCP Hub | http://localhost:8090 |
| LiteLLM Proxy | http://localhost:4000 |
| Temporal UI | http://localhost:8233 |
| Langfuse | http://localhost:3001 |
| MinIO Console | http://localhost:9001 |
| Milvus gRPC | localhost:19530 |

运维细节、排障与 curl 示例见 **[`docs/runbook.md`](docs/runbook.md)**。

## 目录结构

```text
Somna/
├── apps/
│   ├── web/              Next.js 前端
│   ├── bff/              Node.js BFF
│   ├── agent-core/       Python Agent Core (LangGraph + Claude SDK)
│   ├── mcp-hub/          Python MCP 工具网关
│   ├── temporal-worker/  Python Temporal workflows
│   └── sandbox/          Docker 沙盒镜像
├── packages/
│   ├── shared-types/     跨语言 schema
│   ├── event-schema/     Agent → UI 事件协议
│   ├── prompts/          提示词版本化
│   └── i18n/             文案
├── infra/
│   ├── litellm/          LiteLLM config.yaml
│   ├── postgres/init/    PG 初始化 SQL
│   ├── mysql/init/       MySQL 初始化 SQL
│   ├── nginx/            生产 Nginx 镜像与 TLS 模板（见 deployment-nginx-https.md）
│   └── ...
├── docs/
│   ├── prd.md            产品需求
│   ├── architecture.md   架构设计
│   ├── ui-design.md      前端布局（Manus 风格）
│   ├── runbook.md        运维手册
│   ├── deployment-nginx-https.md  生产 Nginx + HTTPS（Compose 叠加）
│   └── adr/              架构决策记录
└── scripts/
    ├── bootstrap.sh      一键准备
    └── health_check.sh   健康检查
```

## 文档

- [产品需求 PRD](docs/prd.md)
- [架构设计](docs/architecture.md)
- [前端布局（Manus 风格）](docs/ui-design.md)
- [运维手册](docs/runbook.md)
- [生产 Nginx + HTTPS](docs/deployment-nginx-https.md)
- [架构决策记录 ADR](docs/adr/)

## 里程碑

- **M1 MVP** — 单轮 Agent + 单容器沙盒（规划中）
- **M2 多会话** — Temporal + mem0 + 多沙盒（**当前**）
- **M3 Manus 对标** — Planner/Executor 分离 + 桌面自动化 + 产物导出
- **M4 生产化** — 计费 + 多模型路由 + K8s + 审计

## 许可证

待定。

## 致谢

思路与 README 结构参考 [Open Master](https://github.com/open-master/open-master) 等开源项目的产品化文档组织方式。技术栈感谢 **LangGraph**、**Claude Agent SDK**、**LiteLLM**、**Temporal**、**mem0**、**MCP** 与 **Vercel / shadcn** 生态。
