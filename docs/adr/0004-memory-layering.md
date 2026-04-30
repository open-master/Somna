# ADR 0004 — 记忆分层：Redis / mem0 / Milvus / Postgres

- **日期**: 2026-04-23
- **状态**: Accepted

## 背景

Agent 要像"熟悉你的同事"：记得偏好、历史结论、相关文档、成功流程。单一向量库做不到；单靠 LLM 上下文也放不下长期记忆。

## 决策

四层记忆，**各司其职不混用**：

| 层 | 存储 | 粒度 | 内容 | TTL | 检索方式 |
|---|---|---|---|---|---|
| Working | Redis | 秒 | scratchpad、tool 结果缓存 | session 结束即清 | key 直取 |
| Episodic | mem0（后端 Milvus）+ Postgres 元数据 | 条 | 用户偏好 / 事实 / 结论 | 永久 | 语义 + 过滤 |
| Semantic | Milvus（主）+ pgvector（小规模兜底） | chunk | 用户文档、网页快照、RAG 语料 | 永久 | 向量 + rerank |
| Procedural | Postgres JSONB | trace | 成功流程模板 / SOP | 永久 | tag + 相似度 |

## 关键配置

### mem0
```python
config = {
    "llm": {"provider": "litellm", "config": {
        "model": "agent-cheap", "api_base": LITELLM_URL}},
    "embedder": {"provider": "litellm", "config": {
        "model": "agent-embed", "api_base": LITELLM_URL}},
    "vector_store": {"provider": "milvus", "config": {
        "host": MILVUS_HOST, "port": MILVUS_PORT, "collection_name": "mem0_memories"}},
}
```

### Embedding 策略
- 主：`qwen3-embedding`（DashScope `text-embedding-v3`，1024 维）
- 备：`bge-m3`（硅基流动，1024 维）—— 两者维度一致，切换无痛
- Rerank：`bge-reranker-v2-m3`

### 索引
- Milvus：`HNSW` (M=16, ef=200) + `IP` 度量
- pgvector：`ivfflat cosine lists=100`，仅用于数据量 < 10w chunks 的小集合

## 使用场景

| 场景 | 走哪一层 |
|---|---|
| "我之前喜欢用什么框架" | Episodic（mem0） |
| "帮我找 tesla 财报里提过哪几款车" | Semantic（Milvus） |
| "按上次生成 HTML 报告的流程再做一份" | Procedural（Postgres） |
| "tool 调用结果缓存 5 分钟避免重复 curl" | Working（Redis） |

## 什么时候从 pgvector 迁移到 Milvus

- 单集合 chunks 超过 1000 万
- p95 检索延迟超过 200ms
- 或需要多字段过滤 + 向量混合搜索

## 影响

- mem0 的写入放在 LangGraph `reflect` 节点，避免每条消息都写
- Procedural 的写入由 Critic 判定任务成功后写入
- 前端"长期记忆"页面从 mem0 API 直接拉
