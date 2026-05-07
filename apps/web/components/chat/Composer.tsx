"use client";
import { useState, useCallback, type KeyboardEvent } from "react";
import { Paperclip, Send } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import {
  interruptSession,
  patchSessionTitle,
  postMessage,
  waitUntilSessionAllowsMessage,
} from "@/lib/api/sessions";
import { getExecutorEngine } from "@/lib/executor-engine";
import { useChatStore } from "@/lib/store/chat";
import { useSessionStore } from "@/lib/store/session";
import { DEFAULT_SESSION_TITLE, isDefaultSessionTitle, titleFromUserMessage } from "@/lib/session-title";

export function Composer({ sessionId }: { sessionId: string }) {
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const pushUser = useChatStore((s) => s.pushUser);
  const rollbackLastUserMessage = useChatStore((s) => s.rollbackLastUserMessage);
  const setPhase = useSessionStore((s) => s.setPhase);
  const setRunId = useSessionStore((s) => s.setRunId);
  const upsertSession = useSessionStore((s) => s.upsertSession);

  const [sendError, setSendError] = useState<string | null>(null);

  const send = useCallback(async () => {
    const value = text.trim();
    if (!value || sending) return;
    setSendError(null);
    setSending(true);
    pushUser(value);
    setText("");
    const execEngine = getExecutorEngine();
    try {
      const active = new Set(["planning", "executing", "compacting"]);
      const phaseNow = useSessionStore.getState().phase;
      if (active.has(phaseNow)) {
        try {
          const ir = await interruptSession(sessionId, "user_interrupt");
          if (ir.interrupted) {
            setPhase("interrupted");
          }
        } catch (e) {
          console.warn("send.pre_interrupt", e);
        }
        await waitUntilSessionAllowsMessage(sessionId);
      }

      let resp: Awaited<ReturnType<typeof postMessage>>;
      try {
        resp = await postMessage(sessionId, value, [], execEngine);
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e);
        if (!msg.includes("409")) throw e;
        try {
          const ir = await interruptSession(sessionId, "user_interrupt");
          if (ir.interrupted) setPhase("interrupted");
        } catch (ie) {
          console.warn("send.conflict_recovery", ie);
        }
        await waitUntilSessionAllowsMessage(sessionId);
        resp = await postMessage(sessionId, value, [], execEngine);
      }

      setPhase("planning");
      setRunId(resp.run_id);
      const existing = useSessionStore.getState().sessions.find((session) => session.id === sessionId);
      const currentTitle = existing?.title ?? DEFAULT_SESSION_TITLE;
      let titleToUse = currentTitle;
      if (isDefaultSessionTitle(currentTitle)) {
        const derived = titleFromUserMessage(value);
        if (!isDefaultSessionTitle(derived)) {
          titleToUse = derived;
          void patchSessionTitle(sessionId, derived).catch(() => {
            upsertSession({
              id: sessionId,
              title: currentTitle,
              createdAt: existing?.createdAt,
              workflowId: existing?.workflowId ?? null,
              status: "running",
              runId: resp.run_id,
              lastRunTerminal: null,
              awaitingUser: false,
              updatedAt: new Date().toISOString(),
            });
          });
        }
      }
      upsertSession({
        id: sessionId,
        title: titleToUse,
        createdAt: existing?.createdAt,
        workflowId: existing?.workflowId ?? null,
        status: "running",
        runId: resp.run_id,
        lastRunTerminal: null,
        awaitingUser: false,
        updatedAt: new Date().toISOString(),
      });
    } catch (e) {
      rollbackLastUserMessage();
      setText(value);
      setSendError(e instanceof Error ? e.message : "发送失败");
    } finally {
      setSending(false);
    }
  }, [sending, sessionId, pushUser, rollbackLastUserMessage, setPhase, setRunId, upsertSession]);

  const onKey = useCallback(
    (e: KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key !== "Enter" || e.shiftKey) return;
      if (e.nativeEvent.isComposing) return;
      e.preventDefault();
      void send();
    },
    [send],
  );

  return (
    <div className="mx-auto w-full max-w-3xl px-4 pb-4">
      <div className="rounded-2xl border bg-card shadow-sm focus-within:ring-2 focus-within:ring-ring/40 transition">
        <Textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKey}
          placeholder="问点什么，或交给我一个任务..."
          rows={2}
          className="border-0 shadow-none min-h-[60px] px-4 py-3 text-sm focus-visible:ring-0 resize-none"
        />
        <div className="flex items-center justify-between px-3 pb-2">
          <div className="flex min-w-0 flex-1 flex-col gap-0.5">
            <div className="flex items-center gap-1">
              <Button size="icon" variant="ghost" aria-label="attach">
                <Paperclip className="size-4" />
              </Button>
              <span className="text-xs text-muted-foreground">
                Enter 发送 · Shift + Enter 换行 · 执行中也会先中断再发新任务
              </span>
            </div>
            {sendError ? <p className="pl-1 text-xs text-destructive">{sendError}</p> : null}
          </div>
          <Button
            size="sm"
            onClick={() => void send()}
            disabled={sending || !text.trim()}
            className="gap-1"
            type="button"
          >
            <Send className="size-3.5" /> {sending ? "发送中…" : "发送"}
          </Button>
        </div>
      </div>
    </div>
  );
}
