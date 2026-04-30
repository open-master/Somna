# apps/agent-core

Somna AI 的大脑：FastAPI + LangGraph + Claude Agent SDK + mem0 + LangChain 组件胶水。

## 责任边界

| 模块 | 职责 |
|---|---|
| `api/` | REST + SSE endpoint，业务对外接口 |
| `graph/` | LangGraph 状态机（ingest → plan → execute → compact → reflect → finalize） |
| `agent/` | Claude Agent SDK 封装（M2 暂用 `llm/client.py` 的 OpenAI 路径，M3 切到 Claude SDK） |
| `llm/` | LiteLLM 客户端封装，暴露 `get_chat()` / `get_embeddings()` |
| `events/` | AgentEvent 生产器 + NATS/Postgres 双写 |
| `storage/` | asyncpg 池、redis、nats、S3 |
| `memory/` | mem0 集成 |
| `rag/` | LangChain 组件拼装（loader / splitter / retriever） |
| `observability/` | Langfuse + OTel |

## 本地开发

构建镜像：
```bash
# 在 repo 根目录
docker compose build agent-core
docker compose up -d agent-core
```

直接裸跑（需要先起其他依赖）：
```bash
cd apps/agent-core
pip install uv
uv pip install -e .
export $(grep -v '^#' ../../.env | xargs)
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

## M2 Smoke 路径（已落地）

1. `POST /v1/sessions` — 创建会话
2. `POST /v1/sessions/{id}/messages` — 发一条消息（异步触发 LangGraph 运行）
3. `GET  /v1/sessions/{id}/stream` — SSE，实时接收 AgentEvent

LangGraph 节点（M2 最小版）：
```
ingest → execute → finalize
         │ 调 LiteLLM OpenAI 兼容端点（Kimi/Qwen/DeepSeek）
         │ 流式输出 message.delta 事件
         └ NATS/Postgres 双写
```

后续 M2 补：`plan`、`compact`、`reflect` 三个节点 + mem0 + Temporal workflow 包装。
