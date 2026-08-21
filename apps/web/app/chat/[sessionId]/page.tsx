"use client";
import { useEffect, useMemo } from "react";
import { useParams } from "next/navigation";

import { AppShell } from "@/components/layout/AppShell";
import { ChatCenter } from "@/components/chat/ChatCenter";
import { LiveComputerPanel } from "@/components/live/LiveComputerPanel";
import { useEventStream } from "@/lib/events/useEventStream";
import type { HandlerMap } from "@somna/event-schema/dispatcher";
import { getSession } from "@/lib/api/sessions";
import { useChatStore } from "@/lib/store/chat";
import { useLiveStore } from "@/lib/store/live";
import { usePlanStore } from "@/lib/store/plan";
import { useSessionStore } from "@/lib/store/session";
import { useSkillDebugStore } from "@/lib/store/skillDebug";
import { useTaskFrameStore } from "@/lib/store/taskFrame";

/** 与会话行 `sessions.status` 对齐：`done` 仅表示本轮 run 结束，会话仍 `active`，可继续发消息。 */
function statusFromPhase(phase: string): string {
  switch (phase) {
    case "planning":
    case "executing":
    case "compacting":
      return "running";
    case "waiting_user":
      return "active";
    case "done":
    case "partial":
      return "active";
    case "error":
      return "error";
    case "interrupted":
      return "interrupted";
    case "stopped":
      return "stopped";
    default:
      return "active";
  }
}

export default function ChatSessionPage() {
  const params = useParams<{ sessionId: string }>();
  const sessionId = params?.sessionId ?? null;

  const setCurrent = useSessionStore((s) => s.setCurrent);
  const setPhase = useSessionStore((s) => s.setPhase);
  const setRunId = useSessionStore((s) => s.setRunId);
  const upsert = useSessionStore((s) => s.upsertSession);

  const chatClear = useChatStore((s) => s.clear);
  const planClear = usePlanStore((s) => s.clear);
  const liveClear = useLiveStore((s) => s.clear);
  const skillDebugClear = useSkillDebugStore((s) => s.clear);
  const taskFrameClear = useTaskFrameStore((s) => s.clear);

  useEffect(() => {
    if (!sessionId) return;
    setCurrent(sessionId);
    chatClear();
    planClear();
    liveClear();
    skillDebugClear();
    taskFrameClear();
    void getSession(sessionId).then((s) =>
      upsert({
        id: s.id,
        title: s.title ?? "新会话",
        status: s.status,
        runId: s.run_id ?? null,
        workflowId: s.workflow_id ?? null,
        createdAt: s.created_at,
        updatedAt: s.updated_at ?? new Date().toISOString(),
      }),
    ).catch(() => { /* ignore for now */ });
    return () => {
      setPhase("idle");
      setRunId(null);
    };
  }, [sessionId, setCurrent, chatClear, planClear, liveClear, skillDebugClear, taskFrameClear, upsert, setPhase, setRunId]);

  const handlers = useMemo<HandlerMap>(() => {
    const chat = useChatStore.getState();
    const plan = usePlanStore.getState();
    const live = useLiveStore.getState();
    const session = useSessionStore.getState();
    const skillDebug = useSkillDebugStore.getState();
    const taskFrame = useTaskFrameStore.getState();
    return {
      "message.delta": (e) => { chat.onMessageDelta(e); live.track(e); },
      "thinking.delta": (e) => { chat.onThinkingDelta(e); live.track(e); },
      "tool.call": (e) => { chat.onToolCall(e); live.onToolCall(e); live.track(e); },
      "tool.result": (e) => { chat.onToolResult(e); live.onToolResult(e); live.track(e); },
      artifact: (e) => { chat.onArtifact(e); live.onArtifact(e); live.track(e); },
      "plan.update": (e) => { plan.onPlanUpdate(e); live.track(e); },
      "task.frame": (e) => {
        taskFrame.fromEvent(e);
        live.track(e);
      },
      "skill.debug": (e) => {
        skillDebug.fromEvent(e);
        live.track(e);
      },
      screenshot: (e) => { live.onScreenshot(e); live.track(e); },
      "token.usage": (e) => { live.onUsage(e); live.track(e); },
      status: (e) => {
        if (e.phase === "planning") {
          useChatStore.getState().beginAssistantTurn();
        }
        session.setPhase(e.phase);
        if (e.run_id) session.setRunId(e.run_id);
        const existing = session.sessions.find((item) => item.id === sessionId);
        const existingTerminal = existing?.lastRunTerminal ?? null;
        let lastRunTerminal: typeof existingTerminal = existingTerminal;
        let nextAwaiting = false;
        const p = e.phase;
        if (p === "planning" || p === "executing" || p === "compacting") {
          lastRunTerminal = null;
        } else if (p === "done") {
          lastRunTerminal = "success";
        } else if (p === "partial") {
          lastRunTerminal = "partial";
        } else if (p === "error") {
          lastRunTerminal = "error";
        } else if (p === "waiting_user") {
          lastRunTerminal = null;
          nextAwaiting = true;
        } else if (p === "interrupted" || p === "stopped") {
          lastRunTerminal = null;
        }
        session.upsertSession({
          id: sessionId,
          title: existing?.title ?? "新会话",
          createdAt: existing?.createdAt,
          workflowId: existing?.workflowId ?? null,
          status: statusFromPhase(e.phase),
          runId: e.run_id ?? existing?.runId ?? null,
          lastRunTerminal,
          awaitingUser: nextAwaiting,
          updatedAt: new Date().toISOString(),
        });
        live.track(e);
      },
      "interrupt.ack": (e) => {
        session.setPhase(e.reason === "user_stop" ? "stopped" : "interrupted");
        if (e.run_id) session.setRunId(e.run_id);
        const existing = session.sessions.find((item) => item.id === sessionId);
        session.upsertSession({
          id: sessionId,
          title: existing?.title ?? "新会话",
          createdAt: existing?.createdAt,
          workflowId: existing?.workflowId ?? null,
          status: e.reason === "user_stop" ? "stopped" : "interrupted",
          runId: e.run_id ?? existing?.runId ?? null,
          lastRunTerminal: null,
          awaitingUser: false,
          updatedAt: new Date().toISOString(),
        });
        live.track(e);
      },
      error: (e) => {
        chat.pushAssistantNotice(
          `运行未完成：${e.message}${e.retryable ? "\n\n你可以调整要求后重新发送，或直接重试本次任务。" : ""}`,
        );
        session.setPhase("error");
        const existing = session.sessions.find((item) => item.id === sessionId);
        session.upsertSession({
          id: sessionId,
          title: existing?.title ?? "新会话",
          createdAt: existing?.createdAt,
          workflowId: existing?.workflowId ?? null,
          status: "error",
          runId: existing?.runId ?? null,
          lastRunTerminal: "error",
          awaitingUser: false,
          updatedAt: new Date().toISOString(),
        });
        live.track(e);
      },
    };
  }, [sessionId]);

  useEventStream(sessionId, handlers);

  const title = useSessionStore((s) =>
    s.sessions.find((x) => x.id === s.currentId)?.title ?? "新会话",
  );

  return (
    <AppShell
      title={title}
      subtitle="Agent Workspace"
      center={sessionId ? <ChatCenter sessionId={sessionId} /> : null}
      right={<LiveComputerPanel sessionId={sessionId ?? ""} />}
    />
  );
}
