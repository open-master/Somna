# apps/mcp-hub

Somna AI — **MCP Hub**：Agent 的"四肢"。

这是一个独立的 FastAPI 服务，对 Agent Core 暴露一组工具，让 Agent 能执行 shell、读写文件、搜索网页等。

## 职责

| 责任 | 说明 |
|---|---|
| **工具注册** | 每个工具暴露 JSON Schema，Agent Core 拉一次就能转成 OpenAI function calling 协议 |
| **调度执行** | `POST /v1/tools/{name}/invoke` 同步执行并返回结构化结果 |
| **沙箱隔离** | 每个会话一个工作目录（M2 基于目录 + 路径越狱防护；M3 升级为 per-session 容器） |
| **可观测** | 返回 `duration_ms`、`preview`、`full_ref`（大输出落 S3） |

## 不属于本服务

- LLM 调用（属于 Agent Core）
- 状态/记忆（属于 Agent Core / mem0）
- 浏览器 VNC / noVNC 流（属于独立的 sandbox 容器，M3 接入）

## 工具清单（M2）

| name | 描述 |
|---|---|
| `shell` | 在沙箱工作目录下执行 bash 命令，带超时与输出截断 |
| `filesystem` | 读 / 写 / 追加 / 列表 / stat / 删除，带路径越狱防护 |
| `search` | 网页搜索：`brave`（`BRAVE_SEARCH_API_KEY`）、`duckduckgo`、`tavily`、`serper`、`mock` |

后续（M3+）会加：`browser`（Playwright/CDP）、`code_exec`（Jupyter kernel）、`rag`（Milvus 检索）。

## 目录

```text
app/
├── main.py                FastAPI entry + lifespan
├── config.py              Settings (env)
├── logging_setup.py       structlog
├── api/
│   ├── health.py
│   ├── tools.py           列 + 调
│   └── sandbox.py         建 / 删 / 信息
├── sandbox/manager.py     SandboxManager
└── tools/
    ├── base.py            BaseTool + ToolResult
    ├── registry.py        全局注册表 + 装饰器
    ├── shell.py
    ├── filesystem.py
    └── search.py
```

## 本地运行

工具与沙盒 API 需要 `Authorization: Bearer $MCP_HUB_INTERNAL_TOKEN`（与 agent-core 共用）。`/healthz` 无需鉴权。

```bash
cd apps/mcp-hub
uv pip install -e .[dev]      # 或者 pip install -e .[dev]
export MCP_HUB_INTERNAL_TOKEN=dev-token
uvicorn app.main:app --port 8090 --reload

# 列工具
curl -H "Authorization: Bearer $MCP_HUB_INTERNAL_TOKEN" localhost:8090/v1/tools | jq

# 建沙箱
curl -sX POST localhost:8090/v1/sandbox \
  -H 'content-type: application/json' \
  -H "Authorization: Bearer $MCP_HUB_INTERNAL_TOKEN" \
  -d '{"session_id":"demo"}' | jq

# 跑 shell
curl -sX POST localhost:8090/v1/tools/shell/invoke \
  -H 'content-type: application/json' \
  -H "Authorization: Bearer $MCP_HUB_INTERNAL_TOKEN" \
  -d '{"sandbox_id":"demo","args":{"cmd":"echo hello && pwd"}}' | jq
```

## 与 Agent Core 的契约

Agent Core 下一批会接入：

1. 启动时拉一次 `GET /v1/tools`，缓存 schema
2. 调用 LLM 时把 schema 转成 OpenAI function calling 格式
3. LLM 返回 tool_call → Agent Core 调 `POST /v1/tools/{name}/invoke`
4. 结果回灌 LLM，同时通过 NATS 广播 `tool.call` / `tool.result` 事件
