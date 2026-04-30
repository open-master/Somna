# ADR 0001 — Monorepo 目录布局

- **日期**: 2026-04-23
- **状态**: Accepted

## 背景

Somna AI 同时包含 Python（FastAPI / LangGraph / Temporal worker）、Node.js（BFF）、Next.js（Web）、Docker 镜像（Sandbox）、基础设施配置（Postgres / MySQL / LiteLLM）等多语言、多运行时的代码与配置。

选项：
1. 多仓（每个服务独立 repo）
2. Monorepo（单 repo 多 app）
3. 混合（核心放 monorepo，sandbox 单独 repo）

## 决策

采用 **Monorepo**，根目录下三顶级目录：

- `apps/` — 可独立部署的服务
- `packages/` — 跨 app 共享的 schema / 协议 / 提示词 / i18n
- `infra/` — 基础设施配置（LiteLLM / PG / MySQL 初始化）
- `scripts/` / `docs/` 辅助

## 理由

- **统一版本**：Agent 事件 schema 在后端 Python 和前端 TS 必须同步，monorepo 最省心
- **一次 docker compose up**：全栈本地开发，贴合 M2 单机部署
- **单一 CI**：一套 pipeline 覆盖多语言
- **渐进拆分可能**：未来任一 app 可以 `git filter-repo` 拆出去

## 影响

- 不使用 Turborepo / pnpm workspace 这类 JS 专用工具，保持语言无关
- 共享 schema 靠 codegen：Python 用 Pydantic 生 JSON schema → TS 用 `json-schema-to-typescript` 生类型
