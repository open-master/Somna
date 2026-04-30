# packages/

跨 app 共享的无运行时资产。

| 目录 | 内容 |
|---|---|
| `event-schema/` | Agent → UI 事件协议（Pydantic + zod 双份，见 ADR 0005） |
| `shared-types/` | 其他跨语言 DTO |
| `prompts/` | 提示词版本化（`prompts/{role}/{version}.md`） |
| `i18n/` | 文案 JSON（zh-CN 优先） |

跨语言同步策略：以 Pydantic 为单一真相源 → codegen 出 JSON schema → ts 端用 `json-schema-to-typescript` 或 zod 生成。
