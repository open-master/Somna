"use client";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import {
  Activity,
  CheckCircle2,
  Clock3,
  MessagesSquare,
  PlayCircle,
  Plus,
  Search,
  Settings2,
  User2,
  Workflow,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { cn } from "@/lib/utils/cn";
import { createSession, listSessions, type Session } from "@/lib/api/sessions";
import { useSessionStore } from "@/lib/store/session";
import { SettingsDialog } from "@/components/layout/SettingsDialog";

const NAV_ITEMS = [
  { key: "sessions", label: "会话", href: "/", icon: MessagesSquare },
  { key: "temporal", label: "Temporal", href: "/temporal", icon: Workflow },
] as const;

function toSummary(session: Session) {
  return {
    id: session.id,
    title: session.title,
    status: session.status,
    runId: session.run_id ?? null,
    workflowId: session.workflow_id ?? null,
    createdAt: session.created_at,
    updatedAt: session.updated_at ?? new Date().toISOString(),
  };
}

export function Sidebar() {
  const router = useRouter();
  const pathname = usePathname();
  const activeNav = pathname.startsWith("/temporal") ? "temporal" : "sessions";

  const current = useSessionStore((s) => s.currentId);
  const sessions = useSessionStore((s) => s.sessions);
  const upsert = useSessionStore((s) => s.upsertSession);
  const hydrate = useSessionStore((s) => s.hydrateSessions);

  const [query, setQuery] = useState("");
  const [settingsOpen, setSettingsOpen] = useState(false);

  useEffect(() => {
    let cancelled = false;
    void listSessions()
      .then((items) => {
        if (!cancelled) hydrate(items.map(toSummary));
      })
      .catch((err) => {
        console.error("sessions.list.failed", err);
      });
    return () => {
      cancelled = true;
    };
  }, [hydrate]);

  const filteredSessions = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return sessions;
    return sessions.filter((s) => s.title.toLowerCase().includes(needle) || s.id.toLowerCase().includes(needle));
  }, [sessions, query]);

  const runningCount = sessions.filter((s) => s.status === "running").length;
  const completedCount = sessions.filter((s) => s.status === "done").length;
  const waitingCount = sessions.filter((s) => s.status === "active").length;

  async function handleNew() {
    const s = await createSession("新会话");
    upsert(toSummary(s));
    router.push(`/chat/${s.id}`);
  }

  return (
    <aside className="flex w-[296px] shrink-0 flex-col border-r bg-muted/20 backdrop-blur">
      <div className="border-b px-4 pb-4 pt-5">
        <div className="flex items-center gap-3">
          <div className="grid size-10 place-items-center rounded-2xl border bg-background shadow-sm">
            <Activity className="size-4 text-primary" />
          </div>
          <div className="min-w-0">
            <p className="truncate text-sm font-semibold tracking-tight">Somna AI</p>
            <p className="text-xs text-muted-foreground">Autonomous Agent Console</p>
          </div>
        </div>

        <div className="mt-4 space-y-2">
          <Button onClick={handleNew} className="w-full justify-start rounded-xl">
            <Plus className="size-4" />
            新建会话
          </Button>
          <div className="grid grid-cols-2 gap-2">
            {NAV_ITEMS.map((item) => {
              const Icon = item.icon;
              const isActive = item.key === activeNav;
              const href =
                item.key === "sessions"
                  ? current
                    ? `/chat/${current}`
                    : sessions[0]
                      ? `/chat/${sessions[0].id}`
                      : "/"
                  : item.href;

              return (
                <Button
                  key={item.key}
                  asChild
                  variant={isActive ? "secondary" : "ghost"}
                  className={cn(
                    "justify-start rounded-xl border",
                    !isActive && "border-transparent bg-transparent",
                  )}
                >
                  <Link href={href}>
                    <Icon className="size-4" />
                    {item.label}
                  </Link>
                </Button>
              );
            })}
          </div>
        </div>
      </div>

      {activeNav === "sessions" ? (
        <>
          <div className="space-y-3 px-4 py-4">
            <div>
              <p className="text-xs font-medium uppercase tracking-[0.18em] text-muted-foreground">Workspace</p>
              <h2 className="mt-1 text-sm font-semibold">最近会话</h2>
            </div>
            <div className="relative">
              <Search className="absolute left-3 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="搜索标题或会话 ID"
                className="h-10 w-full rounded-xl border border-input bg-background pl-9 pr-3 text-sm outline-none ring-offset-background transition focus-visible:ring-2 focus-visible:ring-ring"
              />
            </div>
          </div>

          <ScrollArea className="flex-1 px-3 pb-3">
            <nav className="space-y-2">
              {filteredSessions.length === 0 ? (
                <div className="rounded-2xl border border-dashed bg-background/70 px-4 py-6 text-sm text-muted-foreground">
                  还没有会话。先创建一个任务工作区，再把目标交给 Agent。
                </div>
              ) : (
                filteredSessions.map((s) => (
                  <Link
                    key={s.id}
                    href={`/chat/${s.id}`}
                    className={cn(
                      "block rounded-2xl border px-3 py-3 transition-colors",
                      current === s.id
                        ? "border-primary/30 bg-primary/5 shadow-sm"
                        : "border-transparent bg-background/70 hover:border-border hover:bg-background",
                    )}
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <p className="truncate text-sm font-medium">{s.title}</p>
                        <p className="mt-1 truncate text-xs text-muted-foreground">{s.id}</p>
                      </div>
                      <SessionStatusDot status={s.status} />
                    </div>
                    <div className="mt-3 flex items-center justify-between text-xs text-muted-foreground">
                      <span>{labelForStatus(s.status)}</span>
                      <span>{formatDate(s.updatedAt)}</span>
                    </div>
                  </Link>
                ))
              )}
            </nav>
          </ScrollArea>
        </>
      ) : (
        <>
          <div className="space-y-3 px-4 py-4">
            <div>
              <p className="text-xs font-medium uppercase tracking-[0.18em] text-muted-foreground">Control Plane</p>
              <h2 className="mt-1 text-sm font-semibold">Temporal 概览</h2>
            </div>
            <div className="grid grid-cols-3 gap-2 text-xs">
              <MetricCard icon={PlayCircle} label="运行中" value={runningCount} />
              <MetricCard icon={Clock3} label="待处理" value={waitingCount} />
              <MetricCard icon={CheckCircle2} label="完成" value={completedCount} />
            </div>
          </div>

          <ScrollArea className="flex-1 px-3 pb-3">
            <div className="space-y-2">
              {sessions.slice(0, 8).map((s) => (
                <Link
                  key={s.id}
                  href="/temporal"
                  className="block rounded-2xl border bg-background/70 px-3 py-3 transition-colors hover:bg-background"
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <p className="truncate text-sm font-medium">{s.title}</p>
                      <p className="mt-1 truncate text-xs text-muted-foreground">{s.workflowId ?? "等待首次运行"}</p>
                    </div>
                    <SessionStatusDot status={s.status} />
                  </div>
                  <p className="mt-3 truncate text-xs text-muted-foreground">
                    {s.runId ? `Run ${s.runId}` : "尚未生成 workflow / run"}
                  </p>
                </Link>
              ))}
            </div>
          </ScrollArea>
        </>
      )}

      <div className="flex items-center gap-3 border-t px-4 py-3">
        <div className="grid size-9 place-items-center rounded-full bg-primary/10 text-primary">
          <User2 className="size-4" />
        </div>
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-medium">Me</p>
          <p className="text-xs text-muted-foreground">Local operator</p>
        </div>
        <Button
          type="button"
          size="icon"
          variant="ghost"
          aria-label="设置"
          onClick={() => setSettingsOpen(true)}
        >
          <Settings2 className="size-4" />
        </Button>
      </div>
      <SettingsDialog open={settingsOpen} onOpenChange={setSettingsOpen} />
    </aside>
  );
}

function MetricCard({
  icon: Icon,
  label,
  value,
}: {
  icon: typeof Activity;
  label: string;
  value: number;
}) {
  return (
    <div className="rounded-2xl border bg-background/80 px-3 py-2">
      <div className="flex items-center gap-1.5 text-muted-foreground">
        <Icon className="size-3.5" />
        <span>{label}</span>
      </div>
      <div className="mt-2 text-lg font-semibold tracking-tight">{value}</div>
    </div>
  );
}

function SessionStatusDot({ status }: { status?: string }) {
  return (
    <span
      className={cn(
        "mt-0.5 inline-flex size-2.5 rounded-full",
        status === "running" && "bg-emerald-500",
        status === "done" && "bg-primary",
        status === "error" && "bg-destructive",
        status === "interrupted" && "bg-amber-500",
        (!status || status === "active" || status === "stopped") && "bg-muted-foreground/40",
      )}
    />
  );
}

function labelForStatus(status?: string) {
  switch (status) {
    case "running":
      return "执行中";
    case "done":
      return "已完成";
    case "error":
      return "出错";
    case "interrupted":
      return "已中断";
    case "stopped":
      return "已停止";
    default:
      return "待执行";
  }
}

function formatDate(value?: string) {
  if (!value) return "--";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "--";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}
