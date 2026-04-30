# @somna/event-schema

Agent → UI 事件协议的**单一真相源**（参见 [ADR 0005](../../docs/adr/0005-event-schema.md)）。

- **Python（SSOT）**：`python/somna_events/` — Pydantic 模型
- **TypeScript 镜像**：`typescript/src/index.ts` — zod schema + TS 类型
- **JSON schema 导出**：`schema/event.schema.json` — 由 Python 侧生成，用于校验 TS 镜像没漂移

## 使用

### 后端 (Python)

```bash
# 在 apps/agent-core 的 pyproject.toml 里
[tool.uv.sources]
somna-events = { path = "../../packages/event-schema/python", editable = true }
```

```python
from somna_events import StatusEvent, SessionPhase, ToolCallEvent

event = StatusEvent(session_id=sid, phase=SessionPhase.executing)
nats.publish(f"session.{sid}", event.model_dump_json().encode())
```

### 前端 (TypeScript)

```bash
# apps/web 的 package.json
"@somna/event-schema": "workspace:*"
```

```ts
import { createDispatcher, parseAgentEvent } from "@somna/event-schema";

const dispatcher = createDispatcher({
  "message.delta": (e) => chatStore.appendText(e),
  "tool.call":     (e) => chatStore.addToolCall(e),
  "plan.update":   (e) => planStore.replace(e.todos),
  "status":        (e) => sessionStore.setPhase(e.phase),
});

for await (const raw of sseEventSource) {
  await dispatcher.handle(JSON.parse(raw));
}
```

## 保持 Python / TS 同步

Python 端是唯一真相源。修改 `events.py` 后：

```bash
cd packages/event-schema/python
uv sync                                         # or: pip install -e .
python -m somna_events.export > ../schema/event.schema.json
```

然后手动同步 `typescript/src/index.ts`（有一组小例子做参考），并运行：

```bash
cd packages/event-schema/typescript
npm install
npm run typecheck
```

**后续增强**（未来做）：用 `json-schema-to-zod` / `openapi-typescript` 做全自动 codegen，彻底消灭手动同步。

## 目录

```text
packages/event-schema/
├── README.md
├── python/
│   ├── pyproject.toml
│   ├── somna_events/
│   │   ├── __init__.py
│   │   ├── events.py           # Pydantic 模型（SSOT）
│   │   └── export.py           # 导出 JSON schema
│   └── tests/
│       └── test_events.py
├── typescript/
│   ├── package.json
│   ├── tsconfig.json
│   └── src/
│       ├── index.ts            # zod schema + 类型
│       └── dispatcher.ts       # 前端 reducer helper
└── schema/
    └── event.schema.json       # 由 Python 侧导出（首次需手动跑）
```

## 新增一个事件类型的步骤

1. 在 `python/somna_events/events.py` 定义新的 `BaseEvent` 子类（加 `type: Literal[...]`）
2. 加入 `AgentEvent = Annotated[Union[...]]`
3. 在 `__init__.py` 导出
4. 在 `typescript/src/index.ts` 镜像新增 zod schema + 加入 `discriminatedUnion`
5. 在 `AgentEventMap` 新增映射
6. 跑 `pytest` 和 `tsc --noEmit`
7. 更新 `schema/event.schema.json`
8. （如需）在前端 dispatcher 里加 handler

## 版本

当前 `AGENT_EVENT_SCHEMA_VERSION = "1"`。破坏性变更需 bump 并保留一个版本的兼容期。
