# ADR 0006 — LLM 网关：LiteLLM Proxy

- **日期**: 2026-04-23
- **状态**: Accepted

## 背景

Somna 同时使用 Kimi（Moonshot）、Qwen（DashScope）、DeepSeek、硅基流动的 bge-m3/reranker，未来可能加 Claude / GPT。
如果业务代码直接调各家 SDK：
- 失败切换要自己写
- 成本统计散在多处
- Prompt / response 日志分散
- 更换模型需要改代码

此外，**Claude Agent SDK 只认 Anthropic 协议**，要让它调国产模型，需要一层协议转换。

## 决策

引入 **LiteLLM Proxy** 作为唯一 LLM 出口，同时暴露两种协议：

- OpenAI 兼容端点 `http://litellm:4000/v1/*` — 给 LangChain / mem0 / 业务代码
- Anthropic 兼容端点 `http://litellm:4000/anthropic/v1/messages` — 给 Claude Agent SDK

所有模型通过 `model_list` 注册，业务代码**只使用别名**（`agent-planner` / `agent-executor` / `agent-coder` / `agent-cheap` / `agent-embed` / `agent-rerank`）。

## 方案细节

### 1. 别名路由（`router_settings.model_group_alias`）
业务解耦具体型号，换模型只改 LiteLLM 配置，不改代码。

### 2. Fallback 链
```yaml
fallbacks:
  - kimi-k2-0905:  [kimi-k2-turbo, qwen3-max, deepseek-chat]
  - qwen3-max:     [qwen-plus, kimi-k2-0905]
  - deepseek-chat: [kimi-k2-0905, qwen-plus]
```
任一上游失败自动切备份。

### 3. 成本与配额
- `max_budget: 500` / 月（USD 口径，国产模型换算后记账）
- Per-user key：LiteLLM 支持虚拟 key + per-key budget，M3 开放 team 套餐时启用

### 4. 缓存
Redis prompt cache，按 `(model, messages_hash)` 命中，默认 TTL 10 分钟（可 per-route 覆盖）。

### 5. 观测
`success_callback: ["langfuse"]` 把每次调用的 input/output/token/cost 发到 Langfuse。

### 6. 兼容性
`drop_params: true` —— 国产模型不支持的 `top_logprobs` / `response_format` / `parallel_tool_calls` 等参数自动丢弃，保证 Claude SDK 发过来的请求不会因字段问题失败。

## 已知限制

- LiteLLM 的 Anthropic→OpenAI 转换对复杂 `tool_use` / `tool_result` 嵌套的保真度有限，需要在 Agent Core 层：
  - 对 tool 并发限制为 1（串行）
  - 校验 tool_call 参数 JSON schema，失败重试
- `prompt caching` / `extended thinking` 等 Anthropic 专属特性对国产模型失效（见 [ADR 0002](./0002-langgraph-claude-sdk-split.md)）

## 影响

- 所有服务的 `ANTHROPIC_BASE_URL` 统一指向 `http://litellm:4000/anthropic`
- 所有 Python 代码用 `openai` SDK + `base_url=LITELLM_URL` 访问 OpenAI 端点
- LangChain 的 `ChatOpenAI` / `OpenAIEmbeddings` 同样指向 LiteLLM
- 未来加模型 = 改 `config.yaml`，不改代码
