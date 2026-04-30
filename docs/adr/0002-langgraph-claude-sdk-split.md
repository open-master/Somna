# ADR 0002 — LangGraph / Claude Agent SDK / LangChain 的职责切分

- **日期**: 2026-04-23
- **状态**: Accepted

## 背景

三个框架功能上有重叠：
- **LangChain** 有 `AgentExecutor`（可以做 ReAct）
- **LangGraph** 可以做任意状态机（也能编排 Agent）
- **Claude Agent SDK** 官方定位就是"开箱即用的 Agent"，内置 tool loop

同时堆叠会互相抢夺控制权，典型问题：
- 两份状态机维护（LangGraph 节点 vs Claude SDK 内部 loop）
- 工具注册重复（LangChain tool + MCP + Claude SDK permission）
- 观测数据分散

## 决策

职责按生命周期划分，**零重叠**：

| 框架 | 时间尺度 | 职责 | 禁区 |
|---|---|---|---|
| **LangGraph** | 会话级（分~小时） | 状态机节点、Postgres checkpoint、interrupt/resume、摘要压缩、分支路由 | 不直接调工具、不做单次 tool loop |
| **Claude Agent SDK** | 决策级（秒~分） | tool use 循环、subagent 派发、permission、context 流式接收 | 不跨会话、不管 checkpoint |
| **LangChain** | 无状态 | document loader、text splitter、retriever、output parser、prompt template | 不用 `AgentExecutor`，不用 `Chain`，不管状态 |

## 额外约束（配合国产模型）

因为选择通过 LiteLLM 的 Anthropic 兼容层把 Claude SDK 打到 Kimi/Qwen/DeepSeek，以下特性**在国产模型上失效**：
- `context editing`
- `auto compaction`
- `prompt caching`
- `extended thinking`
- `computer_use`

这些能力的替代方案由 **LangGraph 节点承担**：
- **压缩**：`compact` 节点调 `agent-cheap` 摘要历史
- **缓存**：LiteLLM 层 redis cache（按 prompt hash）
- **Subagent 分发**：LangGraph 子图（Subgraph）
- **桌面操作**：延后到 M3，用 Playwright + 视觉模型自研

## 代码边界示例

```python
# LangGraph 节点：状态机（外壳）
async def execute(state: SessionState) -> SessionState:
    options = ClaudeAgentOptions(model=state.executor_model, mcp_servers=state.mcp)
    async with ClaudeSDKClient(options=options) as client:   # ← 只在 execute 节点里用
        await client.query(state.current_todo.prompt)
        async for ev in client.receive_messages():
            state.events.append(ev)
    return state

# LangChain：只做组件（无状态）
def build_retriever(session_id: str) -> BaseRetriever:
    embeddings = OpenAIEmbeddings(base_url=LITELLM_URL, model="agent-embed")
    store = Milvus(embedding_function=embeddings, collection_name=f"rag_{session_id}")
    return store.as_retriever(search_kwargs={"k": 8})
```

## 影响

- 升级（单 Agent → Planner/Executor）无需重写，只需 `execute` 节点拆成两个节点
- 观测统一：LangGraph + Claude SDK + LangChain 全部回调到 Langfuse
- 新人理解成本低：三件套各司其职
