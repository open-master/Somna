# ADR 0005 — 统一事件协议（Agent → UI）

- **日期**: 2026-04-23
- **状态**: Accepted

## 背景

Agent 会产生多种"事件"：LLM token 流、工具调用、工具结果、截图、产物、阶段变化、token 用量、打断回执……前端需要实时渲染，后端需要持久化做回放。

如果每个前端组件直接订阅后端各类 API，耦合度高、维护差；缺少统一协议会导致新增一种事件要改十处。

参考：AG-UI（Agent User Interaction Protocol）、OpenAI streaming event、Vercel AI SDK data streams。

## 决策

定义单一事件 discriminated union，所有 Agent 产出的事件都必须符合该 schema，同一条 session 的事件按时间有序地通过：
1. NATS JetStream `session.{id}` subject（实时广播）
2. Postgres `events` 表（持久化，支持回放）

前端按 `type` 分派到对应 zustand slice。

## Schema

```ts
type AgentEvent =
  | { type: "message.delta";   run_id: string; role: "assistant"; text: string }
  | { type: "thinking.delta";  run_id: string; text: string }
  | { type: "tool.call";       run_id: string; id: string; name: string; args: unknown }
  | { type: "tool.result";     run_id: string; id: string; ok: boolean;
                               preview: string; full_ref?: string; duration_ms?: number }
  | { type: "screenshot";      run_id: string; ts: number; url: string; source: "browser"|"desktop" }
  | { type: "artifact";        run_id: string; name: string; mime: string;
                               size_bytes?: number; url: string }
  | { type: "plan.update";     todos: Array<{ id: string; text: string;
                               status: "pending"|"in_progress"|"done"|"failed"|"skipped" }> }
  | { type: "status";          phase: "idle"|"planning"|"executing"|"compacting"|"waiting_user"|"done"|"error";
                               message?: string }
  | { type: "token.usage";     model: string; input: number; output: number; cost_usd: number }
  | { type: "interrupt.ack";   reason: string }
  | { type: "error";           code: string; message: string; retryable?: boolean };
```

每个事件都隐含字段：`session_id`, `seq`（单调递增）, `ts`（ISO8601）。

## 生产者

- `Agent Core` 的 LangGraph 每个节点、Claude SDK stream、MCP tool 结果回调，都调用 `EventEmitter.emit(session_id, event)`
- `EventEmitter` 内部双写 NATS + Postgres

## 消费者

- **BFF**：订阅 NATS，按连接做 SSE/WS 多路复用到前端
- **前端**：收到后按 `type` 分派到对应 slice（见 `apps/web/lib/events/dispatcher.ts`）
- **观测**：Langfuse 接 `tool.*` 和 `token.usage` 做附加 trace

## 回放

- GET `/api/sessions/{id}/events?since={seq}` — 拉历史（分页）
- WS/SSE `/api/sessions/{id}/stream` — 实时，收到后自动补齐 since seq

## 版本演进

- 字段新增：向前兼容
- 字段删除或重命名：必须版本 bump，消息里加 `v: 2`

## 影响

- schema 由 `packages/event-schema/` 统一管理，Python 端用 Pydantic，TS 端用 zod
- 新增事件类型只需更新 schema + 一处 dispatcher
