# Somna AI — 前端设计（参考 Manus 布局）

## 1. 设计目标

- **像桌面共享**：用户能全程看到 Agent 在干什么（浏览器/终端/文件/代码）
- **零噪音对话**：Agent 的"思考"默认折叠，重要产出和工具结果结构化呈现
- **全程可介入**：任何时刻都能打断、纠偏、接管、改 TODO
- **移动友好（次要）**：< 1280px 时自动收起右栏，给浮动按钮

## 2. 三栏整体布局

```text
┌────────────────────────────────────────────────────────────────────┐
│ TopBar · 任务标题 · 进度条 · 阶段 · Token · 成本 · ⏸ 打断 · ⏹ 停止 │ 48px
├────────┬───────────────────────────────┬───────────────────────────┤
│        │                               │  ┌─────────────────────┐ │
│        │  [Planner TODO 折叠卡片]      │  │ 🌐 Browser │ 💻 Shell│ │
│ 会话   │  ├○ done  分析 PDF 结构       │  │ 📁 Files   │ ⌨ Code │ │
│ 列表   │  ├● run   生成摘要            │  │ ✅ Tasks   │ 📜 Logs │ │
│        │  └○ todo  输出 HTML           │  └─────────────────────┘ │
│(260px) │                               │                           │
│        │  [消息流 虚拟滚动]            │  ┌─────────────────────┐ │
│ + 新   │  👤 帮我总结 tesla.pdf        │  │                     │ │
│        │  🤖 我先看文件结构 ▼          │  │  iframe / canvas    │ │
│ 📁 今日│    └ 需要先 ls /workspace     │  │  (noVNC/WebRTC)     │ │
│ ├ A    │  🛠  shell: ls /workspace  ▶ │  │                     │ │
│ ├ B ◉  │  ✅ 5 files found             │  │                     │ │
│ └ C    │  🛠  file.read: tesla.pdf  ▶ │  └─────────────────────┘ │
│        │  🖼  [screenshot]            │  ◀━━━━●━━━━━━━▶ 时间轴    │
│ 📁 昨日│  📎 artifact: summary.html   │  10:21  10:24  10:27     │
│ └ ...  │                               │                           │
│        │  ┌──────────────────────────┐│                           │
│ ⚙ 设置 │  │ 输入框      📎 🤖 ▶ 发送 ││                           │
│ 👤 Me  │  └──────────────────────────┘│                           │
└────────┴───────────────────────────────┴───────────────────────────┘
 Left 260px    Center flex=1 (min 540)    Right 420px (resizable 360~640)
```

## 3. 组件清单

### 3.1 TopBar (`components/layout/TopBar.tsx`)
| 元素 | 组件 | 说明 |
|---|---|---|
| Logo + 当前会话名 | `Button ghost` | 点击返回会话首页 |
| 阶段指示 | `Badge` (planning/executing/compacting/waiting/done) | 颜色区分 |
| 进度条 | `Progress` | 根据 TODO 完成比 |
| 耗时 | 文本 | `02:34` 格式 |
| Token 用量 | `HoverCard` | 悬停展开分模型明细 |
| 成本 | `Badge variant="outline"` | `¥0.42` / `$0.06` |
| 打断按钮 | `Button variant="secondary"` | 温和暂停，可恢复 |
| 停止按钮 | `AlertDialog` + `Button destructive` | 需二次确认 |

### 3.2 Left Sidebar (`components/layout/Sidebar.tsx`)
| 元素 | 组件 | 说明 |
|---|---|---|
| 新建会话 | `Button` 全宽 | 主按钮，icon 在左 |
| 搜索框 | `Command + Input` | `Cmd+K` 打开 |
| 分组 | 自定义 | 今天 / 昨天 / 本周 / 更早 |
| 会话条目 | `ContextMenu`  | 右键：改名/删除/pin/导出 markdown |
| 用户区（底部） | `DropdownMenu + Avatar` | 设置、主题、登出 |

### 3.3 Chat Center (`components/chat/*`)

#### 3.3.1 PlannerTimeline
展示当前会话的 TODO 列表，折叠在消息流上方。

```tsx
<Accordion type="single" collapsible>
  <AccordionItem value="plan">
    <AccordionTrigger>📋 任务计划（3/5 已完成）</AccordionTrigger>
    <AccordionContent>
      <ol>
        <TodoItem status="done"     text="分析 PDF 结构" />
        <TodoItem status="running"  text="生成摘要" />
        <TodoItem status="pending"  text="输出 HTML 报告" />
      </ol>
    </AccordionContent>
  </AccordionItem>
</Accordion>
```

#### 3.3.2 MessageList
- `react-virtuoso` 做虚拟滚动，支持"新消息时自动滚到底"和"上滚则停住"
- 每条 `Message` 带 hover 操作：复制、重跑、标记

#### 3.3.3 消息卡片类型

| 类型 | 组件 | 示例 |
|---|---|---|
| 用户文本 | `UserMessage` | 白底 card，右对齐 |
| Assistant 文本 | `AssistantMessage` | 灰底 card，支持 Markdown + 代码高亮 |
| 思考块 | `ThinkingBlock` (Collapsible) | 默认收起，展开后显示 reasoning text |
| Tool Call | `ToolCallCard` | 图标 + 工具名 + 参数（JSON，可复制）+ 耗时 + 状态徽章 |
| Tool Result | `ToolResultCard` (Tabs) | 三个 tab：Preview / Raw / Screenshot |
| 截图 | `ScreenshotCard` | 点击放大到 dialog |
| Artifact | `ArtifactCard` | 文件图标 + 名称 + 大小 + [预览] [下载] |

#### 3.3.4 Composer (`components/chat/Composer.tsx`)
```tsx
<div className="composer">
  <FileDropZone />
  <Textarea rows={1} autoResize />
  <div className="toolbar">
    <AttachButton />       {/* 📎 */}
    <ModelSelector />      {/* planner / executor 切换 */}
    <SendButton />         {/* Cmd/Ctrl+Enter */}
  </div>
</div>
```

### 3.4 Right Panel: Live Computer (`components/live/*`)

#### 3.4.1 Tabs
```tsx
<Tabs defaultValue="browser">
  <TabsList>
    <TabsTrigger value="browser">🌐 Browser</TabsTrigger>
    <TabsTrigger value="shell">💻 Shell</TabsTrigger>
    <TabsTrigger value="files">📁 Files</TabsTrigger>
    <TabsTrigger value="code">⌨ Code</TabsTrigger>
    <TabsTrigger value="tasks">✅ Tasks</TabsTrigger>
    <TabsTrigger value="logs">📜 Logs</TabsTrigger>
  </TabsList>
  <TabsContent value="browser"><BrowserView /></TabsContent>
  ...
</Tabs>
```

#### 3.4.2 各 Tab 实现
| Tab | 组件 | 技术 |
|---|---|---|
| Browser | `BrowserView` | iframe 套 noVNC（M2），M3 迁 WebRTC |
| Shell | `ShellView` | `xterm.js` 只读，Agent tool stream 回放 |
| Files | `FilesView` | shadcn `Tree` 虚拟文件树，点击调 `/api/sandbox/{id}/files/*` |
| Code | `CodeView` | Monaco Editor + diff |
| Tasks | `TasksView` | TODO 表格 + 耗时/模型/工具次数 |
| Logs | `LogsView` | 结构化日志，按 level / source 过滤 |

#### 3.4.3 TimelineScrubber
```tsx
<div className="timeline">
  <Slider min={0} max={events.length - 1} value={[cursor]}
          onValueChange={v => seek(v[0])} />
  <div className="ticks">{screenshots.map(s => <Tick ts={s.ts} />)}</div>
</div>
```
拖动后右栏切换到该时刻的画面（离线回放，LIVE 模式自动回到最新）。

### 3.5 浮窗

| 组件 | 触发 |
|---|---|
| `InterruptDrawer` | 点击 TopBar 打断按钮 → 抽屉式输入纠偏消息 |
| `ArtifactPreviewDialog` | 点击 artifact → 预览（MD/HTML/Image/Code） |
| `CommandPalette` | `Cmd+K` → 搜索会话 / 快捷动作 |
| `SettingsDialog` | 主题 / 默认模型 / 快捷键 / 数据导出 |

## 4. 事件 → 组件 的映射

```ts
// 前端 store (zustand) 维护会话状态；事件按 type 分派到 slice
type EventHandler = {
  "message.delta":  (e) => chatSlice.appendText(e),
  "thinking.delta": (e) => chatSlice.appendThinking(e),
  "tool.call":      (e) => chatSlice.addToolCall(e),
  "tool.result":    (e) => chatSlice.completeToolCall(e),
  "screenshot":     (e) => liveSlice.addScreenshot(e),
  "artifact":       (e) => artifactSlice.add(e),
  "plan.update":    (e) => planSlice.replace(e.todos),
  "status":         (e) => sessionSlice.setPhase(e.phase),
  "token.usage":    (e) => usageSlice.add(e),
  "interrupt.ack":  (e) => sessionSlice.onInterruptAck(e),
};
```

组件订阅各自 slice，组件间零直接通信。

## 5. 视觉规范

### 5.1 颜色
- 中性色：shadcn 默认 `neutral`（白/灰阶）
- 强调色：`indigo-600` (`#4F46E5`)，与 Manus 的橙红区分
- 状态色：
  - `green-600` done
  - `amber-500` running
  - `slate-400` pending
  - `red-600` error/interrupt

### 5.2 字体
- UI：`Geist Sans`
- 代码/命令：`JetBrains Mono`
- 字号体系：12/14/16/20/28

### 5.3 间距
- 基础 4px 网格：`gap-1/2/3/4/6/8`
- 卡片 padding：`p-4`
- 栏间距：左栏右侧 `border-r`，右栏左侧 `border-l`

### 5.4 动效
- 消息到达：`translate-y-2 opacity-0 → translate-y-0 opacity-100` 200ms ease-out
- Tool call 状态切换：状态徽章颜色 150ms 过渡
- 右栏 tab 切换：`fade-in` 100ms
- 避免：页面级大动画、弹跳

### 5.5 主题
- 跟随系统（默认）/ Light / Dark
- 暗色用 `neutral-950` 背景，`neutral-900` 次级，`neutral-100` 文字

## 6. 响应式

| 断点 | 行为 |
|---|---|
| ≥ 1280px | 三栏默认展开（260 / flex / 420） |
| 960 ~ 1280 | 右栏变 360，可折叠为图标 |
| < 960 | 左栏抽屉化（汉堡菜单），右栏 bottom sheet 弹出 |

## 7. 可访问性

- 所有交互元素有 `aria-label`
- 键盘导航：`Tab` 可达全部控件，`Esc` 关弹窗
- 消息流支持 `Ctrl+↑/↓` 跳到上/下一条用户消息
- 对比度 ≥ WCAG AA

## 8. 国际化

- 文案集中在 `packages/i18n/zh-CN.json`，预留 `en-US.json`
- M2 仅中文

## 9. 前端目录结构（apps/web）

```text
apps/web/
├── app/
│   ├── (auth)/
│   │   ├── login/
│   │   └── register/
│   ├── (chat)/
│   │   ├── layout.tsx           # 三栏 shell
│   │   ├── page.tsx             # 会话首页
│   │   └── chat/[sessionId]/
│   │       └── page.tsx
│   ├── api/                     # Next route（BFF 代理 or 本地轻 API）
│   └── globals.css
├── components/
│   ├── ui/                      # shadcn 原件
│   ├── layout/
│   │   ├── TopBar.tsx
│   │   └── Sidebar.tsx
│   ├── chat/
│   │   ├── PlannerTimeline.tsx
│   │   ├── MessageList.tsx
│   │   ├── UserMessage.tsx
│   │   ├── AssistantMessage.tsx
│   │   ├── ThinkingBlock.tsx
│   │   ├── ToolCallCard.tsx
│   │   ├── ToolResultCard.tsx
│   │   ├── ArtifactCard.tsx
│   │   └── Composer.tsx
│   ├── live/
│   │   ├── LiveComputerPanel.tsx
│   │   ├── BrowserView.tsx
│   │   ├── ShellView.tsx
│   │   ├── FilesView.tsx
│   │   ├── CodeView.tsx
│   │   ├── TasksView.tsx
│   │   ├── LogsView.tsx
│   │   └── TimelineScrubber.tsx
│   └── common/
├── lib/
│   ├── ai/                      # Vercel AI SDK 封装
│   ├── rpc/                     # 调后端
│   ├── events/                  # 事件协议 + dispatcher
│   └── store/                   # zustand slices
├── public/
├── package.json
├── tailwind.config.ts
├── next.config.mjs
└── tsconfig.json
```

## 10. 参考 / 致谢

- Manus（布局灵感）
- shadcn/ui（组件基础）
- Vercel AI SDK（流式）
- xterm.js、Monaco Editor、noVNC、react-virtuoso
