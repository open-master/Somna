# Somna AI — 架构设计

## 1. 概览

Somna AI 按"**大脑 / 躯干 / 四肢 / 记忆 / 神经**"五层解构：

| 层 | 组件 | 说明 |
|---|---|---|
| 大脑 | LangGraph + Claude Agent SDK + LangChain | 会话状态机 + Agent loop + 组件胶水 |
| 躯干 | Temporal | 长任务编排 / 断点续跑 / 人工审批 |
| 四肢 | MCP Hub + Sandbox | 工具网关 + 每会话独立 Docker 环境 |
| 记忆 | mem0 + Milvus + Postgres + Redis | 四层记忆：事实 / 向量 / 状态 / 热缓存 |
| 神经 | NATS + SSE/WS/WebRTC + Langfuse + OTel | 事件总线 + 前端流 + 观测 |

## 2. 架构图（逻辑）

```text
┌─────────────────────────────────────────────────────────────┐
│  Frontend (Next.js + shadcn/ui)                             │
│  Sidebar | Chat Stream | Live Computer                      │
└──▲─────────────▲────────────▲───────────────────────────────┘
 SSE│           WS│        WebRTC│
┌───┴─────────────┴────────────┴───────────────────────────┐
│  BFF (Node.js / Hono)  —— 鉴权 / 限流 / 流复用             │
└──┬───────────────────────────────┬───────────────────────┘
   │ gRPC/HTTP                     │ S3 presigned
┌──▼───────────────────┐ ┌─────────▼─────────┐
│ Agent Core (FastAPI) │ │ MinIO / S3        │
│ ┌──────────────────┐ │ └───────────────────┘
│ │ LangGraph Graph  │ │       ┌──────────────────────┐
│ │ （节点级 §3.2）    │ │◀─────▶│ Temporal Server      │
│ │ Postgres ckpt    │ │       │ (workflow durable)   │
│ └────────┬─────────┘ │       └──────────────────────┘
│          │ execute   │              ┌──────────────┐
│ ┌────────▼─────────┐ │   MCP/HTTP   │ MCP Hub      │
│ │ Claude Agent SDK │─┼─────────────▶│ tool router  │
│ │ (via LiteLLM)    │ │              └──────┬───────┘
│ └──────────────────┘ │                     │
│ ┌──────────────────┐ │                     ▼
│ │ LangChain utils  │ │      ┌─────────────────────────┐
│ │ loader/splitter/ │ │      │ Sandbox (per-session)   │
│ │ retriever/parser │ │      │ chromium + shell +      │
│ └──────────────────┘ │      │ jupyter + MCP servers   │
│ ┌──────────────────┐ │      │ noVNC / WebRTC out      │
│ │ mem0 + Milvus    │ │      └─────────────────────────┘
│ └──────────────────┘ │
└──────────┬───────────┘
           │ LLM
┌──────────▼─────────────────────────────────┐
│ LiteLLM Proxy                              │
│ OpenAI + Anthropic 双兼容端点              │
│ 路由到 Kimi / Qwen / DeepSeek / 硅基流动    │
└────────────────────────────────────────────┘

Shared: Postgres (+pgvector) · MySQL · Milvus · Redis · NATS · Langfuse · OTel
```

## 3. 关键设计决策

### 3.1 三大框架职责切分（核心）

| 框架 | 作用域 | 职责 |
|---|---|---|
| **LangGraph** | 会话级（分钟~小时） | 状态机节点、checkpoint、interrupt/resume、摘要压缩 |
| **Claude Agent SDK** | 单次决策（秒~分钟） | Tool use 循环、subagent、permission |
| **LangChain** | 无状态组件 | document loader、splitter、retriever、output parser |

> 详见 [ADR 0002](./adr/0002-langgraph-claude-sdk-split.md)

### 3.2 LangGraph：路线图 vs 当前实现

文档刻意拆成两层：**路线图**描述目标形态（评审与规划用）；**当前实现**以仓库代码为准（开发与排障用）。二者不一致时，以 `apps/agent-core/app/graph/session_graph.py` 为准。

#### 3.2.1 路线图（目标态）

以下为主干设想：**ingest 后进入规划环，execute 与上下文压缩（compact）可与 reflect 形成多轮闭环**。其中「ingest 内 RAG 预检索」「compact 为独立图节点」等能力与当前代码可能尚未完全对齐，属演进方向。

```text
       ┌─────────┐
START→ │ ingest  │  规范化输入、附件落盘、（目标）RAG 预检索等
       └────┬────┘
            ▼
       ┌─────────┐
       │ plan    │  生成/修订 TODO（agent-planner）
       └────┬────┘
            ▼
       ┌─────────┐    tokens > threshold     ┌──────────┐
       │ execute │ ─────────────────────────▶│ compact  │
       └────┬────┘                           └─────┬────┘
            ▼                                      │
       ┌─────────┐ ◀─────────────────────────────  ┘
       │ reflect │
       └────┬────┘
    done │  │ continue    │ replan
         ▼  ▼             ▼
    ┌────────┐       (回 execute / plan)
    │finalize│
    └───┬────┘
        ▼
       END
```

#### 3.2.2 当前实现（代码：`session_graph.py`）

主图节点与边（Checkpoint 使用 Postgres）：

```text
START → ingest → task_frame ─┬─（需澄清）───→ clarify ──────→ finalize → END
                               ├─（轻量直接答）→ direct_answer → finalize → END
                               ├─（error）───────────────────→ finalize → END
                               └─（全链路）────→ plan → execute ─┬─（error）→ finalize → END
                                                                  └─（正常）──→ reflect ─┬→ execute
                                                                                        ├→ plan
                                                                                        └→ finalize → END
```

- **ingest**：会话沙箱、附件从对象存储写入沙箱、本轮用户消息并入 LangGraph `messages`。
- **task_frame**：任务定调（LLM JSON），决定澄清 / 直接回答 / 进入规划。
- **clarify / direct_answer**：不经 Planner 与工具主环；**direct_answer** 可对图片/PDF 做多模态与正文抽取（实现见 `light_reply.py`）。
- **plan**：产出/更新 TODO；**execute**：按 `executor_engine` 走 **OpenAI Chat Completions 自研 loop** 或 **Claude Agent SDK**（经 LiteLLM），再经 MCP Hub 调用沙箱工具。
- **reflect**：复盘后设定 `next_node`（`execute` / `plan` / `finalize`）。**若 `execute` 结束时已设置 `state.error`，主图不进入 `reflect`，直接 `finalize`。**
- **finalize**：终态事件、`sessions.status` 更新；**当前主图中无独立 `compact` 节点**（摘要/压缩若存在，为节点内或其它模块职责，非本节主环）。

> **说明**：`compact` 仍以路线图中的「独立环上节点」为目标，落地后应回到本节更新「当前实现」示意图。

### 3.3 Temporal 与 LangGraph 的边界

| 层 | 负责 | 不负责 |
|---|---|---|
| Temporal | 会话生命周期、sandbox 启停、跨服务协调、定时/重试、人工审批等待 | LLM tool loop 的内部步骤 |
| LangGraph | LLM tool loop 的每一步推进、上下文压缩 | 跨服务编排 |

一个 session workflow 示意：

```python
@workflow.defn
class SessionWorkflow:
    @workflow.run
    async def run(self, req: SessionRequest):
        sandbox = await workflow.execute_activity(start_sandbox, req.user_id, ...)
        try:
            while not self.done:
                # Agent 一次推理循环作为一个 Activity（可取消/心跳/重试）
                result = await workflow.execute_activity(
                    run_agent_step, req.session_id, sandbox.id,
                    heartbeat_timeout=timedelta(seconds=30),
                    start_to_close_timeout=timedelta(minutes=10),
                )
                if result.finished: self.done = True
        finally:
            await workflow.execute_activity(stop_sandbox, sandbox.id)
```

### 3.4 沙盒（"自己的电脑"）

- 每个 session 起一个 Docker 容器：`somna/sandbox:latest`
- 基镜像：`ubuntu:22.04` + Chromium + Playwright + Python 3.12 + Node 20 + tmux + Jupyter + 常用 CLI (git/ffmpeg/pandoc/libreoffice)
- 容器内常驻一个 MCP server 进程（`apps/sandbox/agent`），对外暴露 6 个工具（shell / filesystem / browser / code_exec / search / rag）
- Agent Core 通过 MCP Hub 连接到容器的 MCP 端口
- 画面通过 noVNC/WebRTC 推给前端（M2 先 noVNC，M3 迁 WebRTC 降延迟）
- 隔离：rootless、seccomp、只读 `/etc/passwd`，出口网络仅允许白名单

> 详见 [ADR 0003](./adr/0003-sandbox-strategy.md)

### 3.5 分层记忆

| 层 | 存储 | 内容 | TTL |
|---|---|---|---|
| Working | Redis | 当前 run 的 scratchpad、tool cache | session |
| Episodic | mem0 (后端 Milvus) + Postgres | 用户偏好/事实/结论 | 永久 |
| Semantic | Milvus 主 + pgvector 兜底 | 用户文档、网页快照、RAG 语料 | 永久 |
| Procedural | Postgres JSONB | 成功 trace / SOP 模板 | 永久 |

- Embedding 主用 `qwen3-embedding`，备用 `bge-m3`（硅基流动），都通过 LiteLLM 调用
- Reranker：`bge-reranker-v2-m3`
- mem0 配置：`llm=agent-cheap` (qwen-turbo)，`embedder=agent-embed`，`vector_store=milvus`

> 详见 [ADR 0004](./adr/0004-memory-layering.md)

### 3.6 LLM 网关：LiteLLM

- **唯一出口**：所有模型调用统一经过 LiteLLM
- **双协议端点**：
  - OpenAI：`http://litellm:4000/v1/*`（给 LangChain / mem0 / 业务代码用）
  - Anthropic：`http://litellm:4000/anthropic/v1/messages`（给 Claude Agent SDK 用）
- **业务别名**：`agent-planner` / `agent-executor` / `agent-coder` / `agent-cheap` / `agent-embed` / `agent-rerank` —— 业务代码只认别名
- **Fallback 链**：任何模型失败自动按配置切备用
- **观测**：success/failure callback → Langfuse
- **成本/缓存**：redis 做 prompt cache + LiteLLM 内置 budget

> 详见 [ADR 0006](./adr/0006-llm-gateway-litellm.md)

### 3.7 事件总线与事件协议

- **Bus**：NATS JetStream，支持持久化、按 subject 分发
- **统一 schema**（见 [ADR 0005](./adr/0005-event-schema.md)）：
  ```ts
  type AgentEvent =
    | { type: "message.delta"; ... }
    | { type: "thinking.delta"; ... }
    | { type: "tool.call" | "tool.result"; ... }
    | { type: "screenshot"; ... }
    | { type: "artifact"; ... }
    | { type: "plan.update"; ... }
    | { type: "status"; ... }
    | { type: "token.usage"; ... }
    | { type: "interrupt.ack"; ... };
  ```
- **路径**：Agent Core 产事件 → NATS subject `session.{id}` → BFF 订阅 → SSE/WS 下推前端

### 3.8 前端

三栏布局、shadcn/ui 为主、参见 [UI 设计](./ui-design.md)。

## 4. 数据流

### 4.1 一条用户消息的完整路径

```text
User types "分析 tesla.pdf"   (Frontend)
      │
      ▼  POST /api/sessions/{id}/messages
BFF rate-limit + auth
      │
      ▼  gRPC/HTTP
Agent Core: enqueue(message)
      │
      ▼
Temporal: signal SessionWorkflow
      │
      ▼
LangGraph: ingest → task_frame →（clarify | direct_answer | plan→execute→reflect…）
      │
      ▼  全链路时：execute 内 OpenAI 或 Claude Agent SDK + MCP
LiteLLM（OpenAI `/v1` 或 Anthropic 兼容）→ 配置模型别名
      │
      ▼ tool_use / tool_result（全链路）
回执行器 loop（OpenAI Chat 或 Agent SDK）→ 流式产出
      │
      ▼ publish events to NATS (session.{id})
BFF subscribed → SSE to client
      │
      ▼ Frontend reducer by event.type
UI updates
```

### 4.2 断线续跑

1. 前端断开 → SSE 关闭，但 Agent Core 不受影响
2. Agent 每步事件写入 Postgres `events` 表（双写：NATS 实时 + Postgres 持久）
3. 前端重连 → 先拉 `events?since=last_seen` 回放历史 → 再订阅新事件
4. 如 Agent Core 进程崩溃：Temporal 会重启 Activity，从 LangGraph 最近的 checkpoint 恢复

## 5. 部署拓扑（M2 单机）

```text
┌─────────────────── Host (Linux/Mac) ─────────────────────┐
│ docker compose (network: somna_net)                      │
│                                                          │
│  web  ─  bff  ─  agent-core  ─  mcp-hub                  │
│                     │                                    │
│                     ├─ litellm ─ redis                   │
│                     ├─ postgres (+pgvector)              │
│                     ├─ mysql                             │
│                     ├─ milvus (+ etcd + minio)           │
│                     ├─ nats                              │
│                     ├─ temporal + temporal-ui            │
│                     ├─ langfuse + langfuse-db            │
│                     └─ minio (app s3)                    │
│                                                          │
│  ╔═ sandbox_net (separate bridge, restricted egress) ══╗ │
│  ║  somna/sandbox:latest × N (one per session)         ║ │
│  ╚═══════════════════════════════════════════════════╝  │
└──────────────────────────────────────────────────────────┘
```

## 6. 可观测性

| 信号 | 工具 | 范围 |
|---|---|---|
| LLM trace | Langfuse | prompt/response/token/成本/latency |
| Agent trace | Langfuse (LangGraph 回调) | 每个节点的输入/输出 |
| App trace | OpenTelemetry → Tempo | HTTP/gRPC/DB span |
| Logs | Loki + Promtail | 所有容器结构化日志 |
| Metrics | Prometheus + Grafana | QPS / error / sandbox 数 / token/s |

## 7. 扩展路径

| 触发条件 | 升级动作 |
|---|---|
| 向量 > 1000 万 | 数据从 pgvector 搬到 Milvus（已预留） |
| 用户 > 1000 QPS | BFF 拆出独立服务，Next.js 只托管静态 |
| Sandbox 压力 | 从 Docker-in-Docker 迁到 K8s + Kata/Firecracker |
| 模型成本飙升 | LiteLLM budget + 动态降级到 cheap 别名 |
| 多租户 | 加上 team / workspace scope，数据分 schema |

## 8. 关联文档

- [PRD](./prd.md)
- [UI 设计](./ui-design.md)
- [运维手册](./runbook.md)
- [ADR 目录](./adr/)
