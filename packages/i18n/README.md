# @somna/i18n

前端文案集中地。M2 仅中文，预留英文 key。

```text
packages/i18n/
├── zh-CN.json     主语言
└── en-US.json     预留（未填充）
```

## 约定

- 用**点号命名空间**分组，如 `chat.composer.placeholder`
- 变量用 `{{var}}` 占位
- 前端用 `react-intl` 或 `i18next` 加载（选型在 Web 骨架里决定）
