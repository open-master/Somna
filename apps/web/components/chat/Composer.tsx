"use client";
import { useState, useCallback, type KeyboardEvent } from "react";
import { Paperclip, Send } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { postMessage } from "@/lib/api/sessions";
import { getExecutorEngine } from "@/lib/executor-engine";
import { useChatStore } from "@/lib/store/chat";
import { useSessionStore } from "@/lib/store/session";

export function Composer({ sessionId }: { sessionId: string }) {
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const phase = useSessionStore((s) => s.phase);
  const pushUser = useChatStore((s) => s.pushUser);
  const setPhase = useSessionStore((s) => s.setPhase);
  const setRunId = useSessionStore((s) => s.setRunId);
  const upsertSession = useSessionStore((s) => s.upsertSession);

  const isBusy =
    sending ||
    (phase !== "idle" &&
      phase !== "done" &&
      phase !== "error" &&
      phase !== "waiting_user");

  const send = useCallback(async () => {
    const value = text.trim();
    if (!value || isBusy) return;
    setSending(true);
    pushUser(value);
    setText("");
    try {
      const resp = await postMessage(sessionId, value, [], getExecutorEngine());
      setPhase("planning");
      setRunId(resp.run_id);
      const existing = useSessionStore.getState().sessions.find((session) => session.id === sessionId);
      upsertSession({
        id: sessionId,
        title: existing?.title ?? "新会话",
        createdAt: existing?.createdAt,
        workflowId: existing?.workflowId ?? null,
        status: "running",
        runId: resp.run_id,
        updatedAt: new Date().toISOString(),
      });
    } finally {
      setSending(false);
    }
  }, [text, isBusy, sessionId, pushUser, setPhase, setRunId, upsertSession]);

  const onKey = useCallback(
    (e: KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
        e.preventDefault();
        void send();
      }
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
          <div className="flex items-center gap-1">
            <Button size="icon" variant="ghost" aria-label="attach">
              <Paperclip className="size-4" />
            </Button>
            <span className="text-xs text-muted-foreground">
              Cmd/Ctrl + Enter 发送
            </span>
          </div>
          <Button size="sm" onClick={() => void send()} disabled={isBusy || !text.trim()} className="gap-1">
            <Send className="size-3.5" /> 发送
          </Button>
        </div>
      </div>
    </div>
  );
}
