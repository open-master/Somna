# Somna AI — 产品需求文档 (PRD)

| 版本 | 日期 | 作者 | 变更说明 |
|---|---|---|---|
| 0.1 | 2026-04-23 | 初稿 | M2 阶段 PRD |

## 1. 产品定位

**Somna AI** 是一个对标 Manus 的通用 Agent 系统：用户下发自然语言任务后，Agent 自主规划、调用工具、在专属虚拟环境里执行，产出网页、报告、代码、数据分析等多模态结果，并支持长时任务（小时级）、断线续跑、人工接管。

**一句话**：给每个用户配一台"会用电脑的 AI 同事"。

## 2. 目标用户

| 用户角色 | 典型场景 | 痛点 |
|---|---|---|
| 独立开发者 / 创业者 | 快速产出 MVP、爬取整理信息、做竞品分析 | 工具链碎片，手工操作重复 |
| 内容/运营从业者 | 批量生成文稿、数据报告、PPT | LLM 单点调用不够，需要带工具闭环 |
| 数据分析 / 研究员 | 从多源数据拉取 → 清洗 → 生成报告 | 脚本重复写，缺乏可交互反馈 |
| 技术选型评估者 | 验证 Agent 平台能力上限 | 想自托管可控版本 |

## 3. 产品原则

1. **任务为王**：用户关心"这件事做完了"，不关心用了哪个模型、哪个工具。
2. **全程可观察**：过程像桌面共享，随时看到 Agent 在干嘛。
3. **可介入不可失控**：用户能随时打断、纠偏、接管；Agent 越界时系统能中止。
4. **国产模型友好**：默认使用 Kimi / Qwen / DeepSeek，数据不出境。
5. **单机起步、可水平扩展**：M2 以 Docker Compose 单机可跑；M4 平滑迁 K8s。

## 4. 范围与非范围（M2）

### 4.1 M2 In-Scope

| # | 能力 | 验收标准 |
|---|---|---|
| F1 | 多用户多会话 | 每用户可并行 ≥ 3 个会话，互不干扰 |
| F2 | 专属沙盒 | 每会话一个 Docker 容器，含 shell/浏览器/文件/代码执行 |
| F3 | Agent 自主循环 | 接收任务 → 规划 → 调工具 → 反思 → 输出 |
| F4 | 断线续跑 | 前端关闭重开后能接上实时进度；后端崩溃重启后能从 checkpoint 恢复 |
| F5 | 分层记忆 | mem0 抽取用户偏好；Milvus 存文档向量；procedural 存成功流程 |
| F6 | 流式交互 | 对话 SSE、工具事件 WS、桌面画面 WebRTC |
| F7 | 产物管理 | Agent 产出的文件自动入 MinIO，前端可预览/下载 |
| F8 | 可观测 | 所有 LLM 调用在 Langfuse 里有 trace，含输入输出、耗时、成本 |
| F9 | 人工介入 | 一键暂停/继续、打断、修改 TODO、直接对话纠偏 |
| F10 | 模型路由 | LiteLLM 按任务类型路由到不同模型；失败自动 fallback |

### 4.2 Out-of-Scope（延后到 M3/M4）

- 计算机视觉级的桌面自动化（像 Claude Computer Use 那样完整点击流）
- 团队协作（多成员编辑同一会话）
- 计费与套餐上线
- 手机/平板原生客户端
- 插件市场 / 第三方 Agent 接入

## 5. 核心用户旅程

### 5.1 主流程：下发一个任务

```text
1. 登录 → 新建会话
2. 输入："帮我分析 tesla.pdf 财报，生成中文摘要 HTML 报告"
   （可同时上传 tesla.pdf）
3. Agent:
   a) 规划 (plan 节点) → 生成 TODO:
      [ ] 读取 PDF 结构
      [ ] 提取关键财务数据
      [ ] 翻译并总结
      [ ] 生成 HTML
   b) 执行 (execute 节点) → 按 TODO 调工具:
      - file.read(tesla.pdf)
      - code_exec(提取表格)
      - llm(翻译摘要)
      - file.write(summary.html)
   c) 反思 (reflect 节点) → 发现翻译漏掉第 3 章 → 补执行
   d) 产出 artifact: summary.html
4. 用户点 artifact 预览 / 下载
```

### 5.2 断线场景

- 用户关闭浏览器 → 后端 Agent 继续跑（Temporal workflow 不受影响）
- 用户重新打开会话 → 前端订阅事件流，展示期间累积的事件 + 实时新事件

### 5.3 介入场景

- 用户看到 Agent 方向不对 → 点「打断」→ 发送纠偏消息 → Agent 在下一个可中断点暂停并吸收新指令 → 重新规划后继续

## 6. 功能需求明细

### 6.1 会话管理
- 列表：左侧列表，按时间分组（今天/昨天/本周/更早）
- 搜索：标题 + 内容全文
- 操作：重命名 / 归档 / 删除 / 导出为 markdown

### 6.2 对话区
- 用户消息：支持文本 + 附件（PDF/图片/zip 等）
- Assistant 消息：支持 markdown + code fence + 折叠"思考块"
- Tool Call 卡片：图标 + 工具名 + 参数预览 + 耗时 + 状态
- Tool Result：可切换 Result / Raw / Screenshot
- Artifact：自带预览 + 下载按钮

### 6.3 Live Computer（右栏）
- Tab：Browser / Shell / Files / Code / Tasks / Logs
- Browser 和 Shell 通过 WebRTC 推画面
- Files 通过 S3 预签名 URL
- 底部时间轴：可拖动回放历史截图

### 6.4 输入控制
- 模型下拉：可选当前会话的 Planner / Executor 模型
- 附件上传：拖拽 / 粘贴
- 发送：Cmd/Ctrl + Enter

### 6.5 顶部信息条
- 当前阶段（planning / executing / compacting / waiting_user / done）
- Token 累计 + 成本（悬停看分模型）
- 运行时长
- 停止按钮

## 7. 非功能需求

| 维度 | 目标 |
|---|---|
| 首屏响应 | ≤ 1.5s |
| LLM 首 token | ≤ 2s |
| 工具调用延迟 | shell / file ≤ 200ms；browser 首次 ≤ 3s |
| 桌面画面延迟 | WebRTC ≤ 300ms |
| 单会话可用时长 | ≥ 4 小时不退出 |
| 同时在线用户数 (单机 M2) | ≥ 20 |
| 故障恢复 | 单服务重启后业务不丢；会话可从 checkpoint 续跑 |
| 数据不出境 | 默认模型均为国内 API，文件存自托管 MinIO |

## 8. 安全 & 合规

- 沙盒隔离：rootless 容器 + seccomp + 网络命名空间 + 资源限制
- 沙盒销毁：idle 30min 自动停，数据保留 7 天
- 密钥：所有第三方 API Key 仅 LiteLLM 可读，业务层不直接接触
- 日志脱敏：PII/密钥在入库前过滤
- 审计：敏感操作（删除会话、导出产物、配额变更）留痕至 `audit_logs`

## 9. 成功度量

| 指标 | M2 目标 |
|---|---|
| 任务完成率（Agent 自主跑完，无需人工接管） | ≥ 60% |
| 平均每任务工具调用次数 | 8 – 30 |
| 平均每任务成本 (国产模型) | ≤ ¥1 |
| 用户周留存 | ≥ 40% |
| NPS | ≥ 30 |

## 10. 风险与依赖

| 风险 | 缓解 |
|---|---|
| 国产模型 tool use 稳定性 | 强 JSON schema 校验 + 自动重试 + 多级 fallback |
| 沙盒逃逸 | rootless + 只读宿主卷 + 出口网络白名单 |
| 长任务 token 爆炸 | LangGraph 的 compact 节点强制摘要 |
| Claude SDK 特性在国产模型失效 | 将压缩/缓存/并发重建到 LangGraph 层 |
| Temporal 学习曲线 | 初期只用 1 个 workflow，活动由 Agent Core 提供 |

## 11. 里程碑

- **M1 MVP** (已设计，未实施)：单轮 Agent，命令行 / 简单 UI
- **M2 多会话** (当前)：本 PRD 覆盖范围
- **M3 Manus 对标**：Planner/Executor 分离、Computer Use、产物导出
- **M4 生产化**：计费、K8s、多租户、审计

## 12. 词汇表

| 词 | 定义 |
|---|---|
| Session | 一次用户与 Agent 的完整对话 |
| Run | 一次 Agent 推理循环，多个 Run 组成 Session |
| Sandbox | 为一个 Session 分配的隔离容器 |
| Tool | 被 Agent 调用的能力单元，通过 MCP 暴露 |
| Artifact | Agent 产出的可交付文件 |
| Planner / Executor | Agent 的规划层 / 执行层角色 |
| Checkpoint | LangGraph 对会话状态的快照 |
| Workflow | Temporal 中的一条长时任务 |
