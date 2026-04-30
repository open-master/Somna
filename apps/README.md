# apps/

可独立部署的服务。M2 阶段各服务的实现将分批落地，当前目录下已有结构占位。

| 目录 | 语言 | 状态 | 说明 |
|---|---|---|---|
| `web/` | TS (Next.js 14) | 待实现 | 前端，三栏布局 |
| `bff/` | TS (Hono) | 待实现 | 鉴权、流复用、BFF |
| `agent-core/` | Python (FastAPI) | 待实现 | LangGraph + Claude SDK + mem0 + LangChain |
| `mcp-hub/` | Python | 待实现 | MCP 工具路由 |
| `temporal-worker/` | Python | 待实现 | Temporal workflow & activities |
| `sandbox/` | Dockerfile + Python | 待实现 | 每会话独立容器镜像 |

先行批次（已完成）：根目录 docker-compose / LiteLLM / 数据库 / 文档。
下一批次：Agent Core 骨架 + 最小 LangGraph 状态机 + Claude SDK 连 LiteLLM 的串通验证。
