"use client";
import * as Dialog from "@radix-ui/react-dialog";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import {
  Activity,
  CheckCircle2,
  Clock3,
  LogOut,
  MessagesSquare,
  MoreHorizontal,
  PanelLeft,
  PanelRight,
  Pencil,
  PlayCircle,
  Plus,
  Search,
  Settings2,
  Trash2,
  User2,
  Workflow,
  X,
} from "lucide-react";

import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import { createSession, deleteSession, listSessions, patchSessionTitle, type Session } from "@/lib/api/sessions";
import { clearAccessTokenCookie } from "@/lib/auth/cookie";
import { meRequest, type AuthUser } from "@/lib/api/auth";
import { cn } from "@/lib/utils/cn";
import { useSessionStore, type SessionSummary } from "@/lib/store/session";
import { useUiStore } from "@/lib/store/ui";
import { SettingsDialog } from "@/components/layout/SettingsDialog";

const NAV_ITEMS = [
  { key: "sessions", label: "会话管理", href: "/", icon: MessagesSquare },
  { key: "temporal", label: "调度管理", href: "/temporal", icon: Workflow },
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

  const sidebarCollapsed = useUiStore((s) => s.sidebarCollapsed);
  const toggleSidebar = useUiStore((s) => s.toggleSidebar);

  const current = useSessionStore((s) => s.currentId);
  const sessions = useSessionStore((s) => s.sessions);
  const upsert = useSessionStore((s) => s.upsertSession);
  const removeSession = useSessionStore((s) => s.removeSession);
  const hydrate = useSessionStore((s) => s.hydrateSessions);

  const [query, setQuery] = useState("");
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [renameTarget, setRenameTarget] = useState<{ id: string; title: string } | null>(null);
  const [renameInput, setRenameInput] = useState("");
  const [deletePromptId, setDeletePromptId] = useState<string | null>(null);
  const [deleteBusy, setDeleteBusy] = useState(false);
  const [errorAlert, setErrorAlert] = useState<string | null>(null);
  const [profile, setProfile] = useState<AuthUser | null>(null);

  useEffect(() => {
    void meRequest().then(setProfile);
  }, [pathname]);

  useEffect(() => {
    let cancelled = false;
    void listSessions()
      .then((items) => {
        if (!cancelled) hydrate(items.map(toSummary));
      })
      .catch((err) => {
        console.error("sessions.list.failed", err);
        if (err instanceof Error && /401|403/.test(err.message)) {
          clearAccessTokenCookie();
          router.push("/login");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [hydrate, router]);

  async function confirmDeleteSession() {
    const id = deletePromptId;
    if (!id) return;
    setDeleteBusy(true);
    try {
      await deleteSession(id);
      removeSession(id);
      setDeletePromptId(null);
      if (current === id) router.push("/");
    } catch (e) {
      setDeletePromptId(null);
      if (e instanceof Error && /401|403/.test(e.message)) {
        clearAccessTokenCookie();
        router.push("/login");
        return;
      }
      setErrorAlert(e instanceof Error ? e.message : "删除失败");
    } finally {
      setDeleteBusy(false);
    }
  }

  async function submitRename() {
    if (!renameTarget) return;
    const title = renameInput.trim();
    if (!title) return;
    try {
      const updated = await patchSessionTitle(renameTarget.id, title);
      upsert(toSummary(updated));
      setRenameTarget(null);
    } catch (e) {
      if (e instanceof Error && /401|403/.test(e.message)) {
        clearAccessTokenCookie();
        router.push("/login");
        return;
      }
      setErrorAlert(e instanceof Error ? e.message : "重命名失败");
    }
  }

  const filteredSessions = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return sessions;
    return sessions.filter((s) => s.title.toLowerCase().includes(needle) || s.id.toLowerCase().includes(needle));
  }, [sessions, query]);

  const runningCount = sessions.filter((s) => s.status === "running").length;
  const completedCount = sessions.filter((s) => s.lastRunTerminal === "success").length;
  const waitingCount = sessions.filter(
    (s) => s.awaitingUser || (s.status === "active" && s.lastRunTerminal !== "success"),
  ).length;

  async function handleNew() {
    try {
      const s = await createSession("新会话");
      upsert(toSummary(s));
      router.push(`/chat/${s.id}`);
    } catch (e) {
      if (e instanceof Error && /401|403/.test(e.message)) {
        clearAccessTokenCookie();
        router.push("/login");
        return;
      }
      console.error("createSession.failed", e);
    }
  }

  return (
    <aside
      className={cn(
        "flex shrink-0 flex-col border-r bg-muted/20 backdrop-blur transition-[width] duration-200 ease-out",
        sidebarCollapsed ? "w-[72px]" : "w-[296px]",
      )}
    >
      <div className={cn("border-b pt-5 pb-4", sidebarCollapsed ? "px-2" : "px-4")}>
        <div
          className={cn(
            "flex w-full items-center",
            sidebarCollapsed ? "flex-col gap-2" : "gap-3",
          )}
        >
          <div className="grid size-10 shrink-0 place-items-center rounded-2xl border bg-background shadow-sm">
            <Activity className="size-4 text-primary" />
          </div>
          {!sidebarCollapsed ? (
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-semibold tracking-tight">Somna</p>
            </div>
          ) : null}
          <Button
            type="button"
            size="icon"
            variant="ghost"
            className={cn("shrink-0 rounded-xl", sidebarCollapsed && "mx-auto")}
            aria-label={sidebarCollapsed ? "展开侧栏" : "收起侧栏"}
            onClick={() => toggleSidebar()}
          >
            {sidebarCollapsed ? <PanelRight className="size-4" /> : <PanelLeft className="size-4" />}
          </Button>
        </div>

        <div className={cn("mt-4 space-y-2", sidebarCollapsed && "flex flex-col items-stretch")}>
          <Button
            onClick={handleNew}
            className={cn(
              "rounded-xl gap-2",
              sidebarCollapsed ? "size-10 w-full justify-center p-0" : "w-full justify-start",
            )}
            title="开始新会话"
          >
            <Plus className="size-4 shrink-0" />
            {!sidebarCollapsed ? "开始新会话" : null}
          </Button>
          <div className={cn(sidebarCollapsed ? "flex flex-col gap-2" : "grid grid-cols-2 gap-2")}>
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
                    sidebarCollapsed ? "size-10 w-full justify-center p-0" : "justify-start rounded-xl border",
                    !isActive && !sidebarCollapsed && "border-transparent bg-transparent",
                    !isActive && sidebarCollapsed && "border-transparent",
                  )}
                  title={item.label}
                >
                  <Link
                    href={href}
                    className={cn(
                      "flex w-full items-center gap-2",
                      sidebarCollapsed && "size-full justify-center gap-0",
                    )}
                  >
                    <Icon className="size-4 shrink-0" />
                    {!sidebarCollapsed ? item.label : <span className="sr-only">{item.label}</span>}
                  </Link>
                </Button>
              );
            })}
          </div>
        </div>
      </div>

      {sidebarCollapsed ? (
        <div className="flex-1 min-h-0" aria-hidden />
      ) : activeNav === "sessions" ? (
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
                filteredSessions.map((s) => {
                  const visual = sessionRowVisual(s);
                  return (
                    <div
                      key={s.id}
                      className={cn(
                        "grid grid-cols-[minmax(0,1fr)_auto] items-stretch rounded-2xl border transition-colors",
                        current === s.id
                          ? "border-primary/30 bg-primary/5 shadow-sm"
                          : "border-transparent bg-background/70 hover:border-border hover:bg-background",
                      )}
                    >
                      <Link href={`/chat/${s.id}`} className="min-w-0 overflow-hidden px-3 py-3">
                        <div className="flex items-start justify-between gap-3">
                          <div className="min-w-0">
                            <p className="truncate text-sm font-medium">{s.title}</p>
                            <p className="mt-1 truncate text-xs text-muted-foreground font-mono">{s.id}</p>
                          </div>
                          <SessionStatusDot variant={visual.dot} />
                        </div>
                        <div className="mt-3 flex items-center justify-between gap-2 text-xs text-muted-foreground">
                          <span className="shrink-0">{visual.label}</span>
                          <span className="shrink-0 tabular-nums">{formatDate(s.updatedAt)}</span>
                        </div>
                      </Link>
                      <div className="flex shrink-0 flex-col items-center justify-start border-l border-border/50 bg-muted/10 py-2 pl-0.5 pr-1.5">
                        <DropdownMenu>
                          <DropdownMenuTrigger asChild>
                            <Button
                              type="button"
                              size="icon"
                              variant="ghost"
                              className="size-8 shrink-0 rounded-lg text-muted-foreground hover:text-foreground"
                              aria-label="会话操作"
                            >
                              <MoreHorizontal className="size-4" />
                            </Button>
                          </DropdownMenuTrigger>
                          <DropdownMenuContent align="end" className="w-44">
                            <DropdownMenuItem
                              className="gap-2"
                              onSelect={() => {
                                setRenameTarget({ id: s.id, title: s.title });
                                setRenameInput(s.title);
                              }}
                            >
                              <Pencil className="size-3.5" /> 重命名
                            </DropdownMenuItem>
                            <DropdownMenuItem
                              className="gap-2 text-destructive focus:text-destructive"
                              onSelect={(e) => {
                                e.preventDefault();
                                setDeletePromptId(s.id);
                              }}
                            >
                              <Trash2 className="size-3.5" /> 删除
                            </DropdownMenuItem>
                          </DropdownMenuContent>
                        </DropdownMenu>
                      </div>
                    </div>
                  );
                })
              )}
            </nav>
          </ScrollArea>
        </>
      ) : (
        <>
          <div className="space-y-3 px-4 py-4">
            <div>
              <p className="text-xs font-medium uppercase tracking-[0.18em] text-muted-foreground">Control Plane</p>
              <h2 className="mt-1 text-sm font-semibold">调度概览</h2>
            </div>
            <div className="grid grid-cols-3 gap-2 text-xs">
              <MetricCard icon={PlayCircle} label="运行中" value={runningCount} />
              <MetricCard icon={Clock3} label="待处理" value={waitingCount} />
              <MetricCard icon={CheckCircle2} label="完成" value={completedCount} />
            </div>
          </div>

          <ScrollArea className="flex-1 px-3 pb-3">
            <div className="space-y-2">
              {sessions.slice(0, 8).map((s) => {
                const visual = sessionRowVisual(s);
                return (
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
                    <SessionStatusDot variant={visual.dot} />
                  </div>
                  <p className="mt-3 truncate text-xs text-muted-foreground">
                    {s.runId ? `Run ${s.runId}` : "尚未生成 workflow / run"}
                  </p>
                </Link>
                );
              })}
            </div>
          </ScrollArea>
        </>
      )}

      <div
        className={cn(
          "flex items-center gap-3 border-t py-3",
          sidebarCollapsed ? "flex-col px-2" : "px-4",
        )}
      >
        <div className="grid size-9 place-items-center rounded-full bg-primary/10 text-primary">
          <User2 className="size-4" />
        </div>
        {!sidebarCollapsed ? (
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm font-medium" title={profile?.email}>
              {profile?.email ?? "…"}
            </p>
            <p className="text-xs text-muted-foreground">
              {profile?.role === "admin" ? "管理员" : "用户"}
            </p>
          </div>
        ) : null}
        <Button
          type="button"
          size="icon"
          variant="ghost"
          aria-label="退出登录"
          title="退出登录"
          onClick={() => {
            clearAccessTokenCookie();
            router.push("/");
          }}
          className={cn(sidebarCollapsed && "w-full")}
        >
          <LogOut className="size-4" />
        </Button>
        <Button
          type="button"
          size="icon"
          variant="ghost"
          aria-label="设置"
          onClick={() => setSettingsOpen(true)}
          className={cn(sidebarCollapsed && "w-full")}
        >
          <Settings2 className="size-4" />
        </Button>
      </div>
      <Dialog.Root
        open={renameTarget !== null}
        onOpenChange={(o) => {
          if (!o) setRenameTarget(null);
        }}
      >
        <Dialog.Portal>
          <Dialog.Overlay className="fixed inset-0 z-50 bg-background/80 backdrop-blur-sm data-[state=open]:animate-in" />
          <Dialog.Content className="fixed left-1/2 top-1/2 z-50 w-full max-w-md -translate-x-1/2 -translate-y-1/2 rounded-2xl border bg-card p-6 shadow-xl outline-none">
            <div className="flex items-start justify-between gap-3">
              <Dialog.Title className="text-lg font-semibold">重命名会话</Dialog.Title>
              <Dialog.Close asChild>
                <Button size="icon" variant="ghost" className="size-8 shrink-0 rounded-lg" aria-label="关闭">
                  <X className="size-4" />
                </Button>
              </Dialog.Close>
            </div>
            <p className="mt-1 text-sm text-muted-foreground">将同步到服务器。</p>
            <div className="mt-4 space-y-3">
              <Input value={renameInput} onChange={(e) => setRenameInput(e.target.value)} className="rounded-xl" placeholder="标题" />
              <div className="flex justify-end gap-2">
                <Dialog.Close asChild>
                  <Button type="button" variant="secondary" className="rounded-xl">
                    取消
                  </Button>
                </Dialog.Close>
                <Button type="button" className="rounded-xl" onClick={() => void submitRename()}>
                  保存
                </Button>
              </div>
            </div>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>

      <AlertDialog
        open={deletePromptId !== null}
        onOpenChange={(open) => {
          if (!open && !deleteBusy) setDeletePromptId(null);
        }}
      >
        <AlertDialogContent className="rounded-2xl">
          <AlertDialogHeader>
            <AlertDialogTitle>删除会话</AlertDialogTitle>
            <AlertDialogDescription>
              确定删除该会话？本地与云端相关数据将硬删除且不可恢复。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={deleteBusy} className="rounded-xl">
              取消
            </AlertDialogCancel>
            <Button
              type="button"
              variant="destructive"
              className="rounded-xl"
              disabled={deleteBusy}
              onClick={() => void confirmDeleteSession()}
            >
              {deleteBusy ? "删除中…" : "确定删除"}
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <AlertDialog open={errorAlert !== null} onOpenChange={(open) => !open && setErrorAlert(null)}>
        <AlertDialogContent className="rounded-2xl">
          <AlertDialogHeader>
            <AlertDialogTitle>无法完成操作</AlertDialogTitle>
            <AlertDialogDescription className="whitespace-pre-wrap break-words">
              {errorAlert ?? ""}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <Button type="button" className="rounded-xl" onClick={() => setErrorAlert(null)}>
              确定
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

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

function sessionRowVisual(s: SessionSummary): { dot: string; label: string } {
  if (s.awaitingUser) return { dot: "waiting_user", label: "等待您补充" };
  if (s.lastRunTerminal === "success") return { dot: "run_success", label: "本轮已完成" };
  if (s.lastRunTerminal === "error") return { dot: "error", label: "上轮出错" };
  switch (s.status) {
    case "running":
      return { dot: "running", label: "执行中" };
    case "error":
      return { dot: "error", label: "出错" };
    case "interrupted":
      return { dot: "interrupted", label: "已中断" };
    case "stopped":
      return { dot: "stopped", label: "已停止" };
    default:
      return { dot: "idle", label: "可继续" };
  }
}

function SessionStatusDot({ variant }: { variant: string }) {
  return (
    <span
      className={cn(
        "mt-0.5 inline-flex size-2.5 rounded-full",
        variant === "running" && "bg-emerald-500",
        variant === "run_success" && "bg-primary",
        variant === "waiting_user" && "bg-amber-500",
        variant === "error" && "bg-destructive",
        variant === "interrupted" && "bg-amber-500",
        variant === "stopped" && "bg-muted-foreground/50",
        variant === "idle" && "bg-muted-foreground/40",
      )}
    />
  );
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
