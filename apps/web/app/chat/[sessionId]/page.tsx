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

function statusFromPhase(phase: string): string {
  switch (phase) {
    case "planning":
    case "executing":
    case "compacting":
      return "running";
    case "done":
      return "done";
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

  useEffect(() => {
    if (!sessionId) return;
    setCurrent(sessionId);
    chatClear();
    planClear();
    liveClear();
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
  }, [sessionId, setCurrent, chatClear, planClear, liveClear, upsert, setPhase, setRunId]);

  const handlers = useMemo<HandlerMap>(() => {
    const chat = useChatStore.getState();
    const plan = usePlanStore.getState();
    const live = useLiveStore.getState();
    const session = useSessionStore.getState();
    return {
      "message.delta": (e) => { chat.onMessageDelta(e); live.track(e); },
      "thinking.delta": (e) => { chat.onThinkingDelta(e); live.track(e); },
      "tool.call": (e) => { chat.onToolCall(e); live.onToolCall(e); live.track(e); },
      "tool.result": (e) => { chat.onToolResult(e); live.onToolResult(e); live.track(e); },
      artifact: (e) => { chat.onArtifact(e); live.onArtifact(e); live.track(e); },
      "plan.update": (e) => { plan.onPlanUpdate(e); live.track(e); },
      screenshot: (e) => { live.onScreenshot(e); live.track(e); },
      "token.usage": (e) => { live.onUsage(e); live.track(e); },
      status: (e) => {
        session.setPhase(e.phase);
        if (e.run_id) session.setRunId(e.run_id);
        const existing = session.sessions.find((item) => item.id === sessionId);
        session.upsertSession({
          id: sessionId,
          title: existing?.title ?? "新会话",
          createdAt: existing?.createdAt,
          workflowId: existing?.workflowId ?? null,
          status: statusFromPhase(e.phase),
          runId: e.run_id ?? existing?.runId ?? null,
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
          updatedAt: new Date().toISOString(),
        });
        live.track(e);
      },
      error: (e) => {
        const existing = session.sessions.find((item) => item.id === sessionId);
        session.upsertSession({
          id: sessionId,
          title: existing?.title ?? "新会话",
          createdAt: existing?.createdAt,
          workflowId: existing?.workflowId ?? null,
          status: "error",
          runId: existing?.runId ?? null,
          updatedAt: new Date().toISOString(),
        });
        live.track(e);
      },
    };
  }, [sessionId]);

  useEventStream(sessionId, handlers);

  const title = useSessionStore((s) =>
    s.sessions.find((x) => x.id === s.currentId)?.title ?? "Somna AI",
  );

  return (
    <AppShell
      title={title}
      subtitle="Agent Workspace"
      center={sessionId ? <ChatCenter sessionId={sessionId} /> : null}
      right={<LiveComputerPanel />}
    />
  );
}
