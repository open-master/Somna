# infra/

基础设施配置（非代码）。

| 目录 | 说明 |
|---|---|
| `litellm/config.yaml` | LLM 网关模型注册 + 别名 + fallback |
| `postgres/init/*.sql` | Postgres 首次初始化 SQL |
| `mysql/init/*.sql` | MySQL 首次初始化 SQL |
| `temporal/` | 预留：Temporal dynamicconfig |
| `milvus/` | 预留：Milvus 自定义配置 |
| `langfuse/` | 预留：Langfuse 自定义配置 |
| `nginx/` | 预留：反向代理（生产） |
