# apps/web

Somna AI 前端 — Next.js 14 App Router + shadcn/ui + Tailwind + zustand。

## 三栏布局（参考 Manus）

```text
┌──────── TopBar ────────────────────┐
│ Sidebar │   Chat Stream   │ Live  │
│ 260px   │   (flex-1)      │ 420px │
└────────┴─────────────────┴────────┘
```

详见 `/docs/ui-design.md`。

## 目录

```text
app/
├── layout.tsx              根 layout，挂字体、主题
├── globals.css             Tailwind v3 + CSS variables
├── page.tsx                首页（新建会话、最近会话）
├── chat/[sessionId]/
│   └── page.tsx            三栏主界面
└── api/health/route.ts     健康检查（供 Docker HEALTHCHECK）

components/
├── ui/                     shadcn 基础原件（button/textarea/card/badge/separator/tabs/scroll-area）
├── layout/
│   ├── AppShell.tsx        三栏 shell
│   ├── TopBar.tsx
│   └── Sidebar.tsx
├── chat/
│   ├── ChatCenter.tsx      汇总消息流 + composer
│   ├── MessageList.tsx
│   ├── UserMessage.tsx
│   ├── AssistantMessage.tsx
│   ├── ToolCallCard.tsx
│   ├── PlannerTimeline.tsx
│   └── Composer.tsx
└── live/
    ├── LiveComputerPanel.tsx  带 tabs 的右侧面板
    ├── ScreenPanel.tsx        屏幕 / 浏览器快照
    ├── TerminalPanel.tsx      shell 输出
    ├── FilesPanel.tsx         产物列表
    └── TracePanel.tsx         事件流轨迹

lib/
├── utils/cn.ts             class-name merger
├── api/sessions.ts         REST wrapper
├── events/useEventStream.ts  SSE hook
└── store/                  zustand slices
    ├── session.ts
    ├── chat.ts
    ├── plan.ts
    └── live.ts
```

## 本地开发

```bash
# 安装依赖（会走 file: 协议指向 packages/event-schema/typescript）
cd apps/web
npm install --legacy-peer-deps

# 跑起来
npm run dev
# → http://localhost:3000
```

API 代理：`next.config.mjs` 会把 `/api/v1/*` 反代到 `NEXT_PUBLIC_API_BASE`（默认 `http://localhost:8080`，BFF 端口），这样本地开发直接用相对路径。SSE 走同一反代。

## 事件协议

`@somna/event-schema` 提供 zod schema 与 `createDispatcher`。每个 slice 订阅自己关心的事件类型。

## 本地仅跑 agent-core 时

把 `.env.local` 里的 `NEXT_PUBLIC_API_BASE` 临时改成 `http://localhost:8000`（agent-core 直连），跳过 BFF。生产务必走 BFF。
