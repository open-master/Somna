# Run 与 Temporal 对齐说明

本文档固定 **LangGraph 单次运行**（`run_id`）与 **Temporal 工作流** 之间的命名与边界，便于排障和对照代码。

## 标识符

| 概念 | 典型形态 | 说明 |
|------|----------|------|
| `session_id` | UUID | 业务会话，对应 `sessions` 表主键。 |
| `run_id` | 如 `run_` + 12 hex | 单次用户任务 / 一次图执行；API 与状态中贯穿使用。 |
| Workflow ID | `session:{session_id}:run:{run_id}` | `SessionRunWorkflow` 实例 id，见 `app/api/temporal.py` 等与入库字段 `workflow_id`。 |
| Task queue | 配置项 `TEMPORAL_TASK_QUEUE` | Worker 与 `start_workflow` 使用同一队列名。 |

同一 `session_id` 可以有多轮 `run_id`；每一轮对应 **一个** Temporal workflow id（重试会换新 `run_id` 时也会换新 workflow id，见 retry 路由）。

## 执行边界

| 层级 | 职责 | 代码入口 |
|------|------|----------|
| Workflow | 启动/等待一次「整段会话图」Activity | `app/temporal/workflows.py` → `SessionRunWorkflow.run` |
| Activity | 在进程内调用 `run_session_graph(...)`，并对 Temporal **打心跳** | `app/temporal/activities.py` → `run_session_graph_activity` |
| LangGraph | 多节点（定调 / 计划 / 执行 / 压缩等）与 checkpoint | `app/graph/runner.py` |

**心跳与超时**（可调）：`app/temporal/workflow_limits.py` — `ACTIVITY_START_TO_CLOSE_TIMEOUT`、`ACTIVITY_HEARTBEAT_TIMEOUT`、`ACTIVITY_MAXIMUM_ATTEMPTS`。Activity 内约每 10s `activity.heartbeat()`，直至 `run_session_graph` 结束。

## 与 Phase B 沙盒产物的关系

执行器在沙盒写入 `.somna/runs/<run_id>/` 下的 `task_frame.json`、`plan.json`、`progress.log` 及 `run_index.json`（路径索引）。这些路径与 **业务 `run_id` 一致**；Temporal workflow id 中 **嵌入了同一 `run_id`**，因此可从 workflow id 解析出用于查沙盒目录的 run 片段。

权威性仍以内省事件与 LangGraph checkpoint 为准；沙盒文件为可调试快照与外部工具发现入口。

## 排障速查

1. **Workflow 显示超时/心跳失败**：检查长工具调用是否阻塞事件循环、Worker 是否存活；必要时调大 `ACTIVITY_HEARTBEAT_TIMEOUT` 或缩短心跳间隔（与 limits 注释一致）。  
2. **run_id 与 DB 不一致**：以会话行 `run_id` / `workflow_id` 为准，比对 API 发起新 run 时的赋值链路。  
3. **恢复语义**：当前竖切以「单次 Activity 跑完整图」为主；更细的断点/Signal 续跑见产品里程碑，不在本文范围。
