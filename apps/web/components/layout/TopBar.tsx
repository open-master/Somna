"use client";
import { useState } from "react";
import { Pause, Square, Sparkles } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { interruptSession } from "@/lib/api/sessions";
import { useLiveStore } from "@/lib/store/live";
import { useSessionStore } from "@/lib/store/session";

const PHASE_LABEL: Record<string, string> = {
  idle: "空闲",
  planning: "规划中",
  executing: "执行中",
  compacting: "压缩上下文",
  waiting_user: "等待用户",
  interrupted: "已中断",
  stopped: "已停止",
  done: "本轮已完成",
  partial: "部分完成",
  error: "出错",
};

const PHASE_VARIANT: Record<string, "default" | "secondary" | "success" | "warning" | "destructive"> = {
  idle: "secondary",
  planning: "warning",
  executing: "default",
  compacting: "warning",
  waiting_user: "warning",
  interrupted: "secondary",
  stopped: "secondary",
  done: "success",
  partial: "warning",
  error: "destructive",
};

export function TopBar({
  title,
  subtitle,
  showSessionControls = true,
}: {
  title: string;
  subtitle?: string;
  showSessionControls?: boolean;
}) {
  const phase = useSessionStore((s) => s.phase);
  const setPhase = useSessionStore((s) => s.setPhase);
  const sessionId = useSessionStore((s) => s.currentId);
  const runId = useSessionStore((s) => s.runId);
  const usage = useLiveStore((s) => s.usage);
  const [pending, setPending] = useState<"interrupt" | "stop" | null>(null);

  const activePhases = new Set(["planning", "executing", "compacting"]);
  const isRunning = typeof phase === "string" && activePhases.has(phase);

  async function handleInterrupt(reason: "user_interrupt" | "user_stop") {
    if (!sessionId) return;
    setPending(reason === "user_interrupt" ? "interrupt" : "stop");
    try {
      const res = await interruptSession(sessionId, reason);
      if (res.interrupted) {
        setPhase(reason === "user_stop" ? "stopped" : "interrupted");
      }
    } catch (err) {
      console.error("interrupt.failed", err);
    } finally {
      setPending(null);
    }
  }

  return (
    <header className="flex h-16 shrink-0 items-center gap-4 border-b bg-background/80 px-5 backdrop-blur">
      <div className="flex min-w-0 items-start gap-3">
        <div className="mt-0.5 grid size-8 place-items-center rounded-xl border bg-background shadow-sm">
          <Sparkles className="size-4 text-primary" />
        </div>
        <div className="min-w-0">
          <div className="truncate text-sm font-semibold tracking-tight">{title}</div>
          <div className="truncate text-xs text-muted-foreground">
            {subtitle ?? "Agent workspace"}
          </div>
        </div>
      </div>

      <div className="ml-auto flex items-center gap-2 text-xs text-muted-foreground">
        <Badge variant={PHASE_VARIANT[phase] ?? "secondary"}>{PHASE_LABEL[phase] ?? phase}</Badge>
        {runId ? <Badge variant="outline">{runId}</Badge> : null}
        {showSessionControls ? (
          <>
            <Separator orientation="vertical" className="mx-1 h-5" />
            <span title="tokens (input / output)">
              tokens <span className="font-mono text-foreground">{usage.input}</span> /{" "}
              <span className="font-mono text-foreground">{usage.output}</span>
            </span>
            <span>·</span>
            <span className="font-mono">${usage.cost.toFixed(4)}</span>
            <Separator orientation="vertical" className="mx-1 h-5" />
            <Button
              size="sm"
              variant="outline"
              className="h-8 min-w-[5.5rem] gap-1.5 font-medium shadow-sm"
              disabled={!isRunning || pending !== null}
              onClick={() => void handleInterrupt("user_interrupt")}
            >
              <Pause className="size-3.5 shrink-0" /> {pending === "interrupt" ? "中断中…" : "中断"}
            </Button>
            <Button
              size="sm"
              variant="destructive"
              className="h-8 min-w-[5.5rem] gap-1.5 font-medium shadow-sm"
              disabled={!isRunning || pending !== null}
              onClick={() => void handleInterrupt("user_stop")}
            >
              <Square className="size-3.5 shrink-0" /> {pending === "stop" ? "停止中…" : "停止"}
            </Button>
          </>
        ) : (
          <Badge variant="outline">Workflow Control Plane</Badge>
        )}
      </div>
    </header>
  );
}
