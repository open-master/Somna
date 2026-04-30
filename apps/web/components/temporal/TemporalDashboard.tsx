"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  Bot,
  Clock3,
  ExternalLink,
  Filter,
  History,
  Loader2,
  Pause,
  PauseCircle,
  PlayCircle,
  RefreshCcw,
  RefreshCw,
  Search,
  TimerReset,
  Workflow,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ScrollArea } from "@/components/ui/scroll-area";
import { interruptSession, listSessionEvents, type SessionEvent } from "@/lib/api/sessions";
import {
  cancelTemporalWorkflow,
  getTemporalWorkflow,
  listTemporalWorkflows,
  retryTemporalWorkflow,
  terminateTemporalWorkflow,
  type LinkedSession,
  type TemporalHistoryEvent,
  type TemporalWorkflow,
  type TemporalWorkflowDetail,
} from "@/lib/api/temporal";
import { cn } from "@/lib/utils/cn";

export function TemporalDashboard() {
  const [workflows, setWorkflows] = useState<TemporalWorkflow[]>([]);
  const [selectedWorkflowId, setSelectedWorkflowId] = useState<string | null>(null);
  const [selectedDetail, setSelectedDetail] = useState<TemporalWorkflowDetail | null>(null);
  const [agentEvents, setAgentEvents] = useState<SessionEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [agentEventsLoading, setAgentEventsLoading] = useState(false);
  const [interrupting, setInterrupting] = useState(false);
  const [actionPending, setActionPending] = useState<"cancel" | "terminate" | "retry" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<"all" | "running" | "problem" | "done">("all");
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [lastRefreshedAt, setLastRefreshedAt] = useState<number | null>(null);

  const selected = useMemo(
    () => workflows.find((workflow) => workflow.workflow_id === selectedWorkflowId) ?? workflows[0] ?? null,
    [selectedWorkflowId, workflows],
  );
  const selectedSession = selectedDetail?.session ?? selected?.session ?? null;
  const temporalHistory = selectedDetail?.history ?? [];

  const stats = useMemo(
    () => ({
      total: workflows.length,
      running: workflows.filter((workflow) => isTemporalRunning(workflow.status)).length,
      waiting: workflows.filter((workflow) => isTemporalPending(workflow.status)).length,
      done: workflows.filter((workflow) => isTemporalDone(workflow.status)).length,
      abnormal: workflows.filter((workflow) => isTemporalProblem(workflow.status)).length,
    }),
    [workflows],
  );

  const filteredWorkflows = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return workflows.filter((workflow) => {
      const matchesFilter =
        filter === "all"
          ? true
          : filter === "running"
            ? isTemporalRunning(workflow.status)
            : filter === "problem"
              ? isTemporalProblem(workflow.status)
              : isTemporalDone(workflow.status);
      if (!matchesFilter) return false;
      if (!needle) return true;
      return [
        workflow.workflow_id,
        workflow.run_id ?? "",
        workflow.workflow_type ?? "",
        workflow.task_queue ?? "",
        workflow.session?.title ?? "",
        workflow.session?.id ?? "",
      ]
        .join(" ")
        .toLowerCase()
        .includes(needle);
    });
  }, [filter, query, workflows]);

  const latestIncident = useMemo(
    () => agentEvents.find((event) => event.type === "error" || event.type === "interrupt.ack") ?? null,
    [agentEvents],
  );
  const lastStatus = useMemo(
    () => agentEvents.find((event) => event.type === "status") ?? null,
    [agentEvents],
  );
  const latestPlan = useMemo(
    () => agentEvents.find((event) => event.type === "plan.update") ?? null,
    [agentEvents],
  );
  const latestTemporalIssue = useMemo(
    () =>
      [...temporalHistory]
        .reverse()
        .find(
          (event) =>
            Boolean(event.reason || event.failure_message) ||
            ["FAILED", "TERMINATED", "TIMED_OUT", "CANCELED", "CANCELLED"].some((token) =>
              (event.event_type ?? "").toUpperCase().includes(token),
            ),
        ) ?? null,
    [temporalHistory],
  );
  const latestTemporalRetry = useMemo(
    () => [...temporalHistory].reverse().find((event) => Boolean(event.retry_state || event.new_execution_run_id)) ?? null,
    [temporalHistory],
  );

  useEffect(() => {
    if (!selectedWorkflowId && workflows[0]) {
      setSelectedWorkflowId(workflows[0].workflow_id);
      return;
    }
    if (selectedWorkflowId && !workflows.some((workflow) => workflow.workflow_id === selectedWorkflowId)) {
      setSelectedWorkflowId(workflows[0]?.workflow_id ?? null);
    }
  }, [selectedWorkflowId, workflows]);

  async function loadWorkflowList() {
    const result = await listTemporalWorkflows(100);
    setWorkflows(result.workflows);
    setLastRefreshedAt(Date.now());
    return result.workflows;
  }

  async function loadWorkflowDetail(workflow: TemporalWorkflow | null) {
    if (!workflow?.workflow_id) {
      setSelectedDetail(null);
      return null;
    }
    setDetailLoading(true);
    try {
      const detail = await getTemporalWorkflow(workflow.workflow_id, {
        runId: workflow.run_id ?? undefined,
        historyLimit: 120,
      });
      setSelectedDetail(detail);
      setLastRefreshedAt(Date.now());
      return detail;
    } finally {
      setDetailLoading(false);
    }
  }

  async function loadAgentEvents(session: LinkedSession | null | undefined) {
    if (!session?.id) {
      setAgentEvents([]);
      return;
    }
    setAgentEventsLoading(true);
    try {
      const events = await listSessionEvents(session.id, 0, 200);
      setAgentEvents([...events].reverse());
      setLastRefreshedAt(Date.now());
    } finally {
      setAgentEventsLoading(false);
    }
  }

  async function refreshAll(targetWorkflowId = selectedWorkflowId) {
    setLoading(true);
    setError(null);
    try {
      const nextWorkflows = await loadWorkflowList();
      const nextSelected =
        nextWorkflows.find((workflow) => workflow.workflow_id === targetWorkflowId) ?? nextWorkflows[0] ?? null;
      setSelectedWorkflowId(nextSelected?.workflow_id ?? null);
      const detail = await loadWorkflowDetail(nextSelected);
      await loadAgentEvents(detail?.session ?? nextSelected?.session ?? null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void refreshAll();
  }, []);

  useEffect(() => {
    if (!selected?.workflow_id) {
      setSelectedDetail(null);
      setAgentEvents([]);
      return;
    }
    let cancelled = false;
    void (async () => {
      try {
        const detail = await loadWorkflowDetail(selected);
        if (!cancelled) {
          await loadAgentEvents(detail?.session ?? selected.session ?? null);
        }
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [selected?.workflow_id, selected?.run_id]);

  useEffect(() => {
    if (!autoRefresh) return;
    const timer = window.setInterval(() => {
      if (document.visibilityState !== "visible") return;
      void refreshAll(selectedWorkflowId);
    }, 5000);
    return () => window.clearInterval(timer);
  }, [autoRefresh, selectedWorkflowId]);

  async function handleInterrupt() {
    if (!selectedSession?.id || !isTemporalRunning(selected?.status)) return;
    setInterrupting(true);
    try {
      await interruptSession(selectedSession.id, "user_interrupt");
      await refreshAll(selected?.workflow_id ?? null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setInterrupting(false);
    }
  }

  async function handleWorkflowAction(action: "cancel" | "terminate" | "retry") {
    if (!selected?.workflow_id) return;
    setActionPending(action);
    setError(null);
    try {
      if (action === "cancel") {
        await cancelTemporalWorkflow(selected.workflow_id, { runId: selected.run_id ?? null });
      } else if (action === "terminate") {
        await terminateTemporalWorkflow(selected.workflow_id, { runId: selected.run_id ?? null });
      } else {
        await retryTemporalWorkflow(selected.workflow_id);
      }
      await refreshAll(selected.workflow_id);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setActionPending(null);
    }
  }

  return (
    <div className="flex h-full min-h-0 flex-col bg-[linear-gradient(180deg,transparent,hsl(var(--muted)/0.24))]">
      <div className="grid gap-3 border-b px-4 py-4 lg:grid-cols-4">
        <StatCard label="全部任务" value={stats.total} icon={Workflow} />
        <StatCard label="运行中" value={stats.running} icon={PlayCircle} accent="text-emerald-600" />
        <StatCard label="待处理" value={stats.waiting} icon={Clock3} accent="text-amber-600" />
        <StatCard label="异常/中断" value={stats.abnormal} icon={AlertTriangle} accent="text-destructive" />
      </div>

      <div className="grid min-h-0 flex-1 gap-4 p-4 xl:grid-cols-[360px_minmax(0,1fr)]">
        <Card className="min-h-0 rounded-[24px]">
          <CardHeader className="border-b pb-4">
            <div className="flex flex-col gap-3">
              <div className="flex items-center justify-between gap-2">
                <div>
                  <CardTitle>Temporal Workflows</CardTitle>
                  <p className="mt-1 text-xs text-muted-foreground">
                    真实读取 Temporal workflow 列表，支持搜索、筛选与自动刷新。
                  </p>
                </div>
                <Button
                  size="icon"
                  variant="ghost"
                  onClick={() => void refreshAll(selectedWorkflowId)}
                  disabled={loading || detailLoading || agentEventsLoading}
                  aria-label="refresh"
                >
                  <RefreshCw className={cn("size-4", (loading || detailLoading || agentEventsLoading) && "animate-spin")} />
                </Button>
              </div>

              <div className="relative">
                <Search className="absolute left-3 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
                <input
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder="搜索 workflow / run / session"
                  className="h-10 w-full rounded-xl border border-input bg-background pl-9 pr-3 text-sm outline-none transition focus-visible:ring-2 focus-visible:ring-ring"
                />
              </div>

              <div className="flex flex-wrap items-center gap-2">
                <Filter className="size-3.5 text-muted-foreground" />
                {([
                  ["all", "全部"],
                  ["running", "运行中"],
                  ["problem", "异常"],
                  ["done", "已完成"],
                ] as const).map(([value, label]) => (
                  <Button
                    key={value}
                    type="button"
                    size="sm"
                    variant={filter === value ? "secondary" : "ghost"}
                    className="rounded-xl"
                    onClick={() => setFilter(value)}
                  >
                    {label}
                  </Button>
                ))}
              </div>

              <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
                <button
                  type="button"
                  className={cn(
                    "inline-flex items-center gap-2 rounded-full border px-3 py-1 transition-colors",
                    autoRefresh ? "border-primary/30 bg-primary/5 text-foreground" : "bg-background hover:bg-muted/30",
                  )}
                  onClick={() => setAutoRefresh((current) => !current)}
                >
                  <RefreshCcw className={cn("size-3.5", autoRefresh && "text-primary")} />
                  自动刷新 {autoRefresh ? "开启" : "关闭"}
                </button>
                <span>最近刷新：{lastRefreshedAt ? formatTime(lastRefreshedAt) : "--"}</span>
              </div>
            </div>
          </CardHeader>
          <CardContent className="min-h-0 p-0">
            <ScrollArea className="h-[calc(100vh-256px)]">
              <div className="space-y-2 p-3">
                {filteredWorkflows.length === 0 ? (
                  <div className="rounded-2xl border border-dashed bg-muted/30 px-4 py-8 text-sm text-muted-foreground">
                    没有匹配的 workflow。可以切换筛选条件，或先在会话页发起一个新任务。
                  </div>
                ) : (
                  filteredWorkflows.map((workflow) => (
                    <button
                      key={`${workflow.workflow_id}:${workflow.run_id ?? "latest"}`}
                      type="button"
                      onClick={() => setSelectedWorkflowId(workflow.workflow_id)}
                      className={cn(
                        "w-full rounded-2xl border px-3 py-3 text-left transition-colors",
                        selected?.workflow_id === workflow.workflow_id
                          ? "border-primary/30 bg-primary/5 shadow-sm"
                          : "bg-background hover:border-border hover:bg-muted/30",
                      )}
                    >
                      <div className="flex items-start justify-between gap-3">
                        <div className="min-w-0">
                          <p className="truncate text-sm font-medium">
                            {workflow.session?.title ?? workflow.workflow_type ?? "SessionRunWorkflow"}
                          </p>
                          <p className="mt-1 truncate text-xs text-muted-foreground">{workflow.workflow_id}</p>
                        </div>
                        <StatusBadge status={workflow.status} />
                      </div>
                      <div className="mt-3 flex items-center justify-between text-xs text-muted-foreground">
                        <span>{workflow.run_id ?? "无 run"}</span>
                        <span>{formatDate(workflow.start_time ?? workflow.execution_time)}</span>
                      </div>
                    </button>
                  ))
                )}
              </div>
            </ScrollArea>
          </CardContent>
        </Card>

        <Card className="min-h-0 rounded-[24px]">
          <CardHeader className="border-b pb-4">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <CardTitle>Workflow 详情</CardTitle>
                <p className="mt-1 text-xs text-muted-foreground">
                  这页同时展示 Temporal 调度历史与 Agent 事件流，方便定位问题发生在哪一层。
                </p>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                {selectedSession ? (
                  <Button asChild variant="outline" className="rounded-xl">
                    <Link href={`/chat/${selectedSession.id}`}>
                      打开会话
                      <ExternalLink className="size-3.5" />
                    </Link>
                  </Button>
                ) : null}
                <Button
                  variant="outline"
                  className="rounded-xl"
                  disabled={!selected || loading || detailLoading || agentEventsLoading || actionPending !== null}
                  onClick={() => void refreshAll(selected?.workflow_id ?? null)}
                >
                  {loading || detailLoading || agentEventsLoading ? (
                    <Loader2 className="size-4 animate-spin" />
                  ) : (
                    <RefreshCw className="size-4" />
                  )}
                  刷新详情
                </Button>
                <Button
                  variant="secondary"
                  className="rounded-xl"
                  disabled={
                    !selectedSession?.id ||
                    !isTemporalRunning(selected?.status) ||
                    interrupting ||
                    actionPending !== null
                  }
                  onClick={() => void handleInterrupt()}
                >
                  {interrupting ? <Loader2 className="size-4 animate-spin" /> : <PauseCircle className="size-4" />}
                  优雅打断
                </Button>
                <Button
                  variant="outline"
                  className="rounded-xl"
                  disabled={!selected || !isTemporalRunning(selected.status) || actionPending !== null || interrupting}
                  onClick={() => void handleWorkflowAction("cancel")}
                >
                  {actionPending === "cancel" ? <Loader2 className="size-4 animate-spin" /> : <Pause className="size-4" />}
                  Cancel
                </Button>
                <Button
                  variant="destructive"
                  className="rounded-xl"
                  disabled={!selected || actionPending !== null || interrupting}
                  onClick={() => void handleWorkflowAction("terminate")}
                >
                  {actionPending === "terminate" ? <Loader2 className="size-4 animate-spin" /> : <AlertTriangle className="size-4" />}
                  Terminate
                </Button>
                <Button
                  variant="secondary"
                  className="rounded-xl"
                  disabled={!selectedSession?.id || isTemporalRunning(selected?.status) || actionPending !== null || interrupting}
                  onClick={() => void handleWorkflowAction("retry")}
                >
                  {actionPending === "retry" ? <Loader2 className="size-4 animate-spin" /> : <RefreshCw className="size-4" />}
                  重试任务
                </Button>
              </div>
            </div>
          </CardHeader>
          <CardContent className="space-y-4 p-4">
            {!selected ? (
              <div className="rounded-2xl border border-dashed bg-muted/30 px-4 py-10 text-sm text-muted-foreground">
                选择左侧一条 workflow 记录，查看它对应的运行详情与控制操作。
              </div>
            ) : (
              <>
                <div className="grid gap-3 xl:grid-cols-4">
                  <DetailCard label="Workflow 状态" value={statusLabel(selected.status)} subvalue={selected.status ?? "--"} />
                  <DetailCard
                    label="Workflow 类型"
                    value={selected.workflow_type ?? "SessionRunWorkflow"}
                    subvalue={selected.task_queue ?? "task queue unknown"}
                  />
                  <DetailCard
                    label="Temporal 诊断"
                    value={latestTemporalIssue ? summarizeTemporalHistoryEvent(latestTemporalIssue) : "暂无异常"}
                    subvalue={latestTemporalIssue?.reason ?? latestTemporalIssue?.failure_message ?? "workflow healthy"}
                  />
                  <DetailCard
                    label="Agent 最新进展"
                    value={lastStatus ? summarizeAgentEvent(lastStatus) : "暂无状态"}
                    subvalue={lastStatus?.type ?? "status"}
                  />
                </div>

                <div className="grid gap-3 lg:grid-cols-2">
                  <InfoBlock label="Workflow ID" value={selected.workflow_id} />
                  <InfoBlock label="Run ID" value={selected.run_id ?? "尚未生成"} />
                  <InfoBlock label="开始时间" value={formatDate(selected.start_time ?? selected.execution_time)} />
                  <InfoBlock label="结束时间" value={formatDate(selected.close_time)} />
                  <InfoBlock label="Session ID" value={selectedSession?.id ?? "未绑定 session"} />
                  <InfoBlock label="命名空间" value={selected.namespace ?? "default"} />
                </div>

                <div className="grid gap-3 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
                  <div className="rounded-3xl border bg-background p-4">
                    <div className="flex items-center justify-between gap-2">
                      <div className="flex items-center gap-2 text-sm font-medium">
                        <History className="size-4 text-primary" />
                        Temporal 历史事件
                      </div>
                      <Badge variant="outline">{detailLoading ? "同步中" : "已同步"}</Badge>
                    </div>
                    <TemporalHistoryTimeline events={temporalHistory} loading={detailLoading} />
                  </div>

                  <div className="rounded-3xl border bg-background p-4">
                    <div className="flex items-center justify-between gap-2">
                      <div className="flex items-center gap-2 text-sm font-medium">
                        <Bot className="size-4 text-primary" />
                        Agent 事件时间线
                      </div>
                      <Badge variant="outline">{agentEventsLoading ? "同步中" : "已同步"}</Badge>
                    </div>
                    <AgentTimeline events={agentEvents} loading={agentEventsLoading} />
                  </div>
                </div>

                <div className="grid gap-3 xl:grid-cols-[minmax(0,1fr)_minmax(320px,0.9fr)]">
                  <div className="rounded-3xl border bg-muted/25 p-4">
                    <div className="flex items-center gap-2 text-sm font-medium">
                      <TimerReset className="size-4 text-primary" />
                      调度层 vs Agent 层
                    </div>
                    <ol className="mt-4 space-y-3 text-sm text-muted-foreground">
                      <li className="flex items-start gap-3">
                        <span className="mt-1 inline-flex size-2.5 rounded-full bg-primary" />
                        Temporal 负责 workflow 生命周期、取消与持久化恢复。
                      </li>
                      <li className="flex items-start gap-3">
                        <span className="mt-1 inline-flex size-2.5 rounded-full bg-sky-500" />
                        Agent 事件流负责展示规划、执行、反思、产物和 token 消耗。
                      </li>
                      <li className="flex items-start gap-3">
                        <span className="mt-1 inline-flex size-2.5 rounded-full bg-emerald-500" />
                        两条线放在同一页，可以快速判断卡在调度层还是 Agent 层。
                      </li>
                    </ol>
                  </div>

                  <div className="space-y-3">
                    <div className="rounded-3xl border bg-muted/25 p-4">
                      <div className="flex items-center gap-2 text-sm font-medium">
                        <AlertTriangle className="size-4 text-amber-600" />
                        最近 Temporal 异常
                      </div>
                      <div className="mt-3 text-sm text-muted-foreground">
                        {latestTemporalIssue ? summarizeTemporalHistoryEvent(latestTemporalIssue) : "最近没有 Temporal 异常事件。"}
                      </div>
                      <div className="mt-2 text-xs text-muted-foreground">
                        {latestTemporalIssue
                          ? `${latestTemporalIssue.event_type ?? "UNKNOWN"} · #${latestTemporalIssue.event_id ?? "-"}`
                          : "健康状态"}
                      </div>
                      {latestTemporalIssue?.failure_chain?.length ? (
                        <ul className="mt-3 space-y-1 text-xs text-muted-foreground">
                          {latestTemporalIssue.failure_chain.slice(0, 3).map((item, index) => (
                            <li key={`${index}-${item}`} className="truncate">
                              {index + 1}. {item}
                            </li>
                          ))}
                        </ul>
                      ) : null}
                    </div>

                    <div className="rounded-3xl border bg-muted/25 p-4">
                      <div className="flex items-center gap-2 text-sm font-medium">
                        <RefreshCw className="size-4 text-primary" />
                        Retry / Continue 线索
                      </div>
                      <div className="mt-3 text-sm text-muted-foreground">
                        {latestTemporalRetry
                          ? latestTemporalRetry.retry_state ?? `new run → ${latestTemporalRetry.new_execution_run_id}`
                          : "最近没有 retry / continued-as-new 线索。"}
                      </div>
                      <div className="mt-2 text-xs text-muted-foreground">
                        {latestTemporalRetry?.new_execution_run_id
                          ? `next run: ${latestTemporalRetry.new_execution_run_id}`
                          : latestTemporalRetry?.event_type ?? "no retry signal"}
                      </div>
                    </div>

                    <div className="rounded-3xl border bg-muted/25 p-4">
                      <div className="flex items-center gap-2 text-sm font-medium">
                        <Bot className="size-4 text-primary" />
                        最近 Agent 异常 / 中断
                      </div>
                      <div className="mt-3 text-sm text-muted-foreground">
                        {latestIncident ? summarizeAgentEvent(latestIncident) : "最近没有 Agent 异常或中断事件。"}
                      </div>
                      <div className="mt-2 text-xs text-muted-foreground">
                        {latestIncident ? `${latestIncident.type} · #${latestIncident.seq ?? "-"}` : "健康状态"}
                      </div>
                    </div>

                    <div className="rounded-3xl border bg-muted/25 p-4">
                      <div className="flex items-center gap-2 text-sm font-medium">
                        <Pause className="size-4 text-primary" />
                        最近计划快照
                      </div>
                      {latestPlan && latestPlan.type === "plan.update" ? (
                        <ul className="mt-3 space-y-2 text-sm text-muted-foreground">
                          {latestPlan.todos.slice(0, 5).map((todo) => (
                            <li key={todo.id} className="flex items-start gap-2">
                              <span className={cn("mt-1 inline-flex size-2 rounded-full", todoDotClass(todo.status))} />
                              <span className="min-w-0 flex-1">{todo.text}</span>
                            </li>
                          ))}
                        </ul>
                      ) : (
                        <p className="mt-3 text-sm text-muted-foreground">还没有收到计划更新事件。</p>
                      )}
                    </div>
                  </div>
                </div>
              </>
            )}

            {error ? (
              <div className="rounded-2xl border border-destructive/30 bg-destructive/5 px-4 py-3 text-sm text-destructive">
                {error}
              </div>
            ) : null}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

function StatCard({
  label,
  value,
  icon: Icon,
  accent,
}: {
  label: string;
  value: number;
  icon: typeof Workflow;
  accent?: string;
}) {
  return (
    <div className="rounded-[22px] border bg-background/80 px-4 py-4 shadow-sm">
      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        <Icon className={cn("size-4", accent)} />
        {label}
      </div>
      <div className="mt-3 text-2xl font-semibold tracking-tight">{value}</div>
    </div>
  );
}

function StatusBadge({ status }: { status?: string | null }) {
  if (isTemporalRunning(status)) return <Badge variant="success">运行中</Badge>;
  if (isTemporalDone(status)) return <Badge variant="default">已完成</Badge>;
  if (isTemporalProblem(status)) return <Badge variant="destructive">异常</Badge>;
  return <Badge variant="outline">待执行</Badge>;
}

function DetailCard({ label, value, subvalue }: { label: string; value: string; subvalue: string }) {
  return (
    <div className="rounded-3xl border bg-background p-4">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="mt-2 text-lg font-semibold tracking-tight">{value}</div>
      <div className="mt-1 text-xs text-muted-foreground">{subvalue}</div>
    </div>
  );
}

function InfoBlock({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-2xl border bg-muted/20 p-4">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="mt-2 break-all font-mono text-sm">{value}</div>
    </div>
  );
}

function TemporalHistoryTimeline({ events, loading }: { events: TemporalHistoryEvent[]; loading: boolean }) {
  if (loading && events.length === 0) {
    return (
      <div className="mt-4 flex items-center gap-2 text-sm text-muted-foreground">
        <Loader2 className="size-4 animate-spin" />
        正在加载 Temporal 历史...
      </div>
    );
  }
  if (events.length === 0) {
    return <div className="mt-4 text-sm text-muted-foreground">还没有拿到 Temporal workflow 历史。</div>;
  }
  return (
    <ScrollArea className="mt-4 h-[320px] pr-3">
      <ol className="space-y-3">
        {events.map((event) => (
          <li key={`${event.event_id ?? "na"}-${event.event_type ?? "type"}`} className="flex gap-3">
            <span className={cn("mt-1 inline-flex size-2.5 shrink-0 rounded-full", temporalToneDotClass(event.event_type))} />
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                <span>{formatEventTs(event.event_time)}</span>
                <span>#{event.event_id ?? "-"}</span>
                <Badge variant="outline" className="rounded-full px-2 py-0">
                  {event.event_type ?? "UNKNOWN"}
                </Badge>
              </div>
              <p className="mt-1 text-sm leading-6">{summarizeTemporalHistoryEvent(event)}</p>
              {event.reason ? <p className="text-xs text-amber-700 dark:text-amber-300">reason: {event.reason}</p> : null}
              {event.failure_message ? (
                <p className="text-xs text-destructive">failure: {event.failure_message}</p>
              ) : null}
              {event.retry_state ? (
                <p className="text-xs text-muted-foreground">retry_state: {event.retry_state}</p>
              ) : null}
              {event.new_execution_run_id ? (
                <p className="text-xs text-muted-foreground">next run: {event.new_execution_run_id}</p>
              ) : null}
            </div>
          </li>
        ))}
      </ol>
    </ScrollArea>
  );
}

function AgentTimeline({ events, loading }: { events: SessionEvent[]; loading: boolean }) {
  if (loading && events.length === 0) {
    return (
      <div className="mt-4 flex items-center gap-2 text-sm text-muted-foreground">
        <Loader2 className="size-4 animate-spin" />
        正在加载 Agent 事件...
      </div>
    );
  }
  if (events.length === 0) {
    return <div className="mt-4 text-sm text-muted-foreground">还没有 replay 到 Agent 事件。</div>;
  }
  return (
    <ScrollArea className="mt-4 h-[320px] pr-3">
      <ol className="space-y-3">
        {events.map((event) => (
          <li key={`${event.seq ?? "na"}-${event.type}`} className="flex gap-3">
            <span className={cn("mt-1 inline-flex size-2.5 shrink-0 rounded-full", agentToneDotClass(event.type))} />
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                <span>{formatEventTs(event.ts)}</span>
                <span>#{event.seq ?? "-"}</span>
                <Badge variant="outline" className="rounded-full px-2 py-0">
                  {event.type}
                </Badge>
              </div>
              <p className="mt-1 text-sm leading-6">{summarizeAgentEvent(event)}</p>
            </div>
          </li>
        ))}
      </ol>
    </ScrollArea>
  );
}

function summarizeAgentEvent(event: SessionEvent): string {
  switch (event.type) {
    case "status":
      return event.message ? `${event.phase}: ${event.message}` : event.phase;
    case "error":
      return `[${event.code}] ${event.message}`;
    case "interrupt.ack":
      return `收到中断确认: ${event.reason}`;
    case "plan.update":
      return `${event.todos.filter((todo) => todo.status === "done").length}/${event.todos.length} 项计划已完成`;
    case "tool.call":
      return `调用工具 ${event.name}`;
    case "tool.result":
      return `${event.ok ? "工具成功" : "工具失败"} · ${event.preview.slice(0, 48)}`;
    case "artifact":
      return `产物 ${event.name}`;
    case "screenshot":
      return "截图已更新";
    case "token.usage":
      return `${event.model} 消耗 ${event.input}/${event.output}`;
    case "message.delta":
      return event.text.slice(0, 60);
    case "thinking.delta":
      return `(thinking) ${event.text.slice(0, 40)}`;
    default:
      return "unknown";
  }
}

function summarizeTemporalHistoryEvent(event: TemporalHistoryEvent): string {
  if (event.summary) return event.summary;
  const type = (event.event_type ?? "").toUpperCase();
  if (type.includes("WORKFLOW_EXECUTION_STARTED")) return "Workflow 已启动";
  if (type.includes("WORKFLOW_TASK_SCHEDULED")) return "Workflow task 已调度";
  if (type.includes("WORKFLOW_TASK_STARTED")) return "Workflow task 已开始";
  if (type.includes("WORKFLOW_TASK_COMPLETED")) return "Workflow task 已完成";
  if (type.includes("ACTIVITY_TASK_SCHEDULED")) return "Activity 已调度";
  if (type.includes("ACTIVITY_TASK_STARTED")) return "Activity 已开始";
  if (type.includes("ACTIVITY_TASK_COMPLETED")) return "Activity 已完成";
  if (type.includes("ACTIVITY_TASK_FAILED")) return "Activity 执行失败";
  if (type.includes("WORKFLOW_EXECUTION_COMPLETED")) return "Workflow 已完成";
  if (type.includes("WORKFLOW_EXECUTION_FAILED")) return "Workflow 失败";
  if (type.includes("WORKFLOW_EXECUTION_CANCELED") || type.includes("WORKFLOW_EXECUTION_CANCELLED")) {
    return "Workflow 已取消";
  }
  if (type.includes("WORKFLOW_EXECUTION_TERMINATED")) return "Workflow 已终止";
  return event.event_type ?? "UNKNOWN_EVENT";
}

function statusLabel(status?: string | null) {
  if (isTemporalRunning(status)) return "运行中";
  if (isTemporalDone(status)) return "已完成";
  switch ((status ?? "").toUpperCase()) {
    case "FAILED":
      return "失败";
    case "CANCELED":
    case "CANCELLED":
      return "已取消";
    case "TERMINATED":
      return "已终止";
    case "TIMED_OUT":
      return "已超时";
    case "CONTINUED_AS_NEW":
      return "继续运行";
    default:
      return "待执行";
  }
}

function temporalToneDotClass(type?: string | null) {
  const normalized = (type ?? "").toUpperCase();
  if (normalized.includes("FAILED") || normalized.includes("TERMINATED")) return "bg-destructive";
  if (normalized.includes("CANCELED") || normalized.includes("CANCELLED")) return "bg-amber-500";
  if (normalized.includes("COMPLETED")) return "bg-emerald-500";
  if (normalized.includes("STARTED") || normalized.includes("SCHEDULED")) return "bg-primary";
  return "bg-muted-foreground/40";
}

function agentToneDotClass(type: SessionEvent["type"]) {
  switch (type) {
    case "error":
      return "bg-destructive";
    case "interrupt.ack":
      return "bg-amber-500";
    case "status":
      return "bg-primary";
    case "tool.call":
    case "tool.result":
      return "bg-sky-500";
    case "artifact":
    case "screenshot":
      return "bg-emerald-500";
    case "plan.update":
      return "bg-violet-500";
    default:
      return "bg-muted-foreground/40";
  }
}

function todoDotClass(status: string) {
  switch (status) {
    case "done":
      return "bg-emerald-500";
    case "in_progress":
      return "bg-amber-500";
    case "failed":
      return "bg-destructive";
    default:
      return "bg-muted-foreground/40";
  }
}

function isTemporalRunning(status?: string | null) {
  return (status ?? "").toUpperCase() === "RUNNING";
}

function isTemporalDone(status?: string | null) {
  return (status ?? "").toUpperCase() === "COMPLETED";
}

function isTemporalProblem(status?: string | null) {
  return ["FAILED", "CANCELED", "CANCELLED", "TERMINATED", "TIMED_OUT"].includes((status ?? "").toUpperCase());
}

function isTemporalPending(status?: string | null) {
  return !status || ["UNSPECIFIED", "PENDING", "NEW"].includes((status ?? "").toUpperCase());
}

function formatDate(value?: string | null) {
  if (!value) return "--";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "--";
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function formatEventTs(value?: string | null) {
  if (!value) return "--";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "--";
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(date);
}

function formatTime(value: number) {
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(new Date(value));
}
