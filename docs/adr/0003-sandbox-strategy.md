# ADR 0003 — 沙盒策略：每会话一个 Docker 容器

- **日期**: 2026-04-23
- **状态**: Accepted

## 背景

Agent "自己的电脑"是对标 Manus 的关键能力。候选：
1. 共享进程（Agent 和用户任务混跑）— ❌ 危险
2. 每会话一个 Docker 容器
3. E2B / Daytona SaaS
4. K8s + Firecracker / Kata microVM

## 决策

**M2 采用方案 2（每会话一个 Docker 容器，自建）**，M4+ 视压力迁 K8s + microVM。

## 理由

| 维度 | Docker 自建 | E2B SaaS | K8s+microVM |
|---|---|---|---|
| 启动延迟 | 1-3s | 0.5s | <1s |
| 隔离强度 | 中（namespace + cgroup） | 强 | 最强 |
| 成本 | 最低 | 按时长计费 | 高（运维） |
| 数据出境 | 无 | 有 | 无 |
| 上手难度 | 低 | 极低 | 高 |
| M2 匹配度 | ✅ | 合规风险 | 过度设计 |

## 镜像规格（`apps/sandbox`）

- 基：`ubuntu:22.04`
- 运行时：Python 3.12、Node 20、Go 1.22（可选）
- 工具：Chromium + Playwright、tmux、git、ffmpeg、pandoc、libreoffice、jq、yq
- 常驻：一个 MCP server（`agent/mcp_entry.py`），对外 6 个工具：
  - `shell` — tmux 持久会话
  - `filesystem` — /workspace 读写
  - `browser` — Playwright 驱动 Chromium
  - `code_exec` — Jupyter kernel (python) + Node REPL
  - `search` — 走外层 search API（Tavily/Serper/Jina）
  - `rag` — 走 Agent Core 的 retriever（MCP 调用）

## 安全

- rootless 启动（`--user 1000`）
- 只读挂载 `/etc/passwd`、`/etc/hosts`
- 网络：独立 bridge `somna_sandbox_net`，出口默认拒绝，白名单 DNS/HTTPS 到常见站点
- 资源：CPU 2 核 / 内存 4GB / pids 200（可 per-plan 调）
- 生命周期：idle > 30min 自动停，数据在 `somna_sandbox_workspace/{session_id}` volume 保留 7 天

## 接入

- Temporal 的 `StartSandboxActivity` 启动容器，把容器 id / MCP 端口 / VNC 端口写回 `sandboxes` 表
- Agent Core 通过 MCP Hub 用 `stdio` 或 `streamable_http` 连接到该容器的 MCP server
- 前端通过 BFF 的 `/sandbox/{id}/vnc` 反代到容器的 noVNC

## 影响

- Docker-in-Docker 模式必须开启（宿主 socket 挂入 agent-core）
- 未来迁 K8s 时只需替换 `StartSandboxActivity` 实现
- M3 如果要做真正的 computer use，镜像里需装 Xvfb + pyautogui
