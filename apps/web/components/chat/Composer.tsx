"use client";
import { useState, useCallback, useRef, type KeyboardEvent } from "react";
import { Paperclip, Send, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import {
  type SessionAttachmentRef,
  interruptSession,
  patchSessionTitle,
  postMessage,
  uploadSessionAttachment,
  waitUntilSessionAllowsMessage,
} from "@/lib/api/sessions";
import { getExecutorEngine } from "@/lib/executor-engine";
import { useChatStore } from "@/lib/store/chat";
import { usePlanStore } from "@/lib/store/plan";
import { useSessionStore } from "@/lib/store/session";
import { useTaskFrameStore } from "@/lib/store/taskFrame";
import { DEFAULT_SESSION_TITLE, isDefaultSessionTitle, titleFromUserMessage } from "@/lib/session-title";

export function Composer({
  sessionId,
  streamReady,
}: {
  sessionId: string;
  streamReady: boolean;
}) {
  const [text, setText] = useState("");
  const [attachments, setAttachments] = useState<SessionAttachmentRef[]>([]);
  const [uploading, setUploading] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const [sending, setSending] = useState(false);
  const pushUser = useChatStore((s) => s.pushUser);
  const rollbackLastUserMessage = useChatStore((s) => s.rollbackLastUserMessage);
  const setPhase = useSessionStore((s) => s.setPhase);
  const setRunId = useSessionStore((s) => s.setRunId);
  const upsertSession = useSessionStore((s) => s.upsertSession);
  const sharedSending = useSessionStore((s) => s.messageSendSessionId !== null);
  const tryBeginMessageSend = useSessionStore((s) => s.tryBeginMessageSend);
  const endMessageSend = useSessionStore((s) => s.endMessageSend);
  const clearPlan = usePlanStore((s) => s.clear);
  const clearTaskFrame = useTaskFrameStore((s) => s.clear);

  const [sendError, setSendError] = useState<string | null>(null);

  const onFiles = useCallback(
    async (e: React.ChangeEvent<HTMLInputElement>) => {
      const list = e.target.files;
      if (!list?.length) return;
      setSendError(null);
      setUploading(true);
      try {
        for (const f of Array.from(list)) {
          const meta = await uploadSessionAttachment(sessionId, f);
          setAttachments((prev) => [...prev, meta]);
        }
      } catch (err) {
        setSendError(err instanceof Error ? err.message : "上传失败");
      } finally {
        setUploading(false);
        e.target.value = "";
      }
    },
    [sessionId],
  );

  const send = useCallback(async () => {
    const value = text.trim();
    if ((!value && attachments.length === 0) || sending) return;
    if (!streamReady) {
      setSendError("正在恢复会话记录，请稍候再发送");
      return;
    }
    if (!tryBeginMessageSend(sessionId)) {
      setSendError("已有消息正在提交，请稍候");
      return;
    }
    setSendError(null);
    setSending(true);
    const pendingAtt = [...attachments];
    const userBubble =
      value || (pendingAtt.length ? `「已添加 ${pendingAtt.length} 个附件」` : "");
    if (userBubble) pushUser(userBubble, pendingAtt.length ? pendingAtt : undefined);
    setText("");
    setAttachments([]);
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
        resp = await postMessage(sessionId, value, pendingAtt, execEngine);
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
        resp = await postMessage(sessionId, value, pendingAtt, execEngine);
      }

      clearPlan();
      clearTaskFrame();
      setPhase("planning");
      setRunId(resp.run_id);
      const existing = useSessionStore.getState().sessions.find((session) => session.id === sessionId);
      const currentTitle = existing?.title ?? DEFAULT_SESSION_TITLE;
      let titleToUse = currentTitle;
      // 仅用用户输入的非空正文作为自动标题：纯附件不参与；首次出现文字的该次写入后标题锁死，直至默认标题被手动改名等
      const userTextForTitle = value.trim();
      if (isDefaultSessionTitle(currentTitle) && userTextForTitle.length > 0) {
        const derived = titleFromUserMessage(userTextForTitle);
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
              lastPhase: null,
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
        lastPhase: null,
        awaitingUser: false,
        updatedAt: new Date().toISOString(),
      });
    } catch (e) {
      rollbackLastUserMessage();
      setText(value);
      setAttachments(pendingAtt);
      setSendError(e instanceof Error ? e.message : "发送失败");
    } finally {
      setSending(false);
      endMessageSend(sessionId);
    }
  }, [
    text,
    attachments,
    sending,
    streamReady,
    sessionId,
    tryBeginMessageSend,
    endMessageSend,
    pushUser,
    rollbackLastUserMessage,
    clearPlan,
    clearTaskFrame,
    setPhase,
    setRunId,
    upsertSession,
  ]);

  const onKey = useCallback(
    (e: KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key !== "Enter" || e.shiftKey) return;
      if (e.nativeEvent.isComposing) return;
      e.preventDefault();
      void send();
    },
    [send],
  );

  const canSend =
    (text.trim().length > 0 || attachments.length > 0) &&
    streamReady &&
    !sharedSending;

  return (
    <div className="mx-auto w-full max-w-3xl px-4 pb-4">
      <input
        ref={fileRef}
        type="file"
        className="sr-only"
        multiple
        onChange={(e) => void onFiles(e)}
        aria-hidden
      />
      <div className="rounded-2xl border bg-card shadow-sm focus-within:ring-2 focus-within:ring-ring/40 transition">
        {attachments.length > 0 ? (
          <div className="flex flex-wrap gap-1.5 border-b px-3 py-2">
            {attachments.map((a) => (
              <span
                key={a.s3_key}
                className="inline-flex max-w-full items-center gap-1 rounded-md bg-muted/80 px-2 py-0.5 text-xs"
              >
                <span className="truncate">{a.filename}</span>
                <button
                  type="button"
                  className="rounded p-0.5 hover:bg-muted"
                  onClick={() => setAttachments((prev) => prev.filter((x) => x.id !== a.id))}
                  aria-label={`移除 ${a.filename}`}
                >
                  <X className="size-3.5 opacity-70" />
                </button>
              </span>
            ))}
          </div>
        ) : null}
        <Textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKey}
          placeholder={streamReady ? "问点什么，或交给我一个任务..." : "正在恢复会话记录…"}
          rows={2}
          className="border-0 shadow-none min-h-[60px] px-4 py-3 text-sm focus-visible:ring-0 resize-none"
        />
        <div className="flex items-center justify-between px-3 pb-2">
          <div className="flex min-w-0 flex-1 flex-col gap-0.5">
            <div className="flex items-center gap-1">
              <Button
                size="icon"
                variant="ghost"
                aria-label="添加附件"
                type="button"
                disabled={uploading || sharedSending || !streamReady}
                onClick={() => fileRef.current?.click()}
              >
                <Paperclip className="size-4" />
              </Button>
              <span className="text-xs text-muted-foreground">
                Enter 发送 · Shift + Enter 换行 · 可先上传附件再补充说明
                {uploading ? " · 上传中…" : ""}
              </span>
            </div>
            {sendError ? <p className="pl-1 text-xs text-destructive">{sendError}</p> : null}
          </div>
          <Button
            size="sm"
            onClick={() => void send()}
            disabled={!canSend}
            className="gap-1"
            type="button"
          >
            <Send className="size-3.5" />{" "}
            {sending ? "发送中…" : streamReady ? "发送" : "恢复中…"}
          </Button>
        </div>
      </div>
    </div>
  );
}
