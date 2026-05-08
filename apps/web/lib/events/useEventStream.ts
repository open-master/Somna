"use client";
import { useEffect, useRef } from "react";
import { createDispatcher } from "@somna/event-schema/dispatcher";
import type { HandlerMap } from "@somna/event-schema/dispatcher";

import { mergeUserMessagesAndEvents, type PersistedUserMessage } from "@/lib/chat/timeline";
import { listAllSessionEventPayloads, listSessionEvents, listSessionMessages } from "@/lib/api/sessions";
import { useChatStore } from "@/lib/store/chat";

const POLL_MS = 1_500;

/**
 * Subscribe to a session's SSE stream.
 *
 * - **Bootstrap**：拉取 DB 用户消息 + 全量 events，按 `created_at` 交错回放后，再以最大 seq 连接 SSE，避免与库表重复。
 *   （Bootstrap 回放时暂不逐条应用 `screenshot`，只在结束时应用最后一张，以免观察区预览图连环换 src 刷屏拉取 artifacts。）
 * - **HTTP poll fallback**：轮询 `GET /events?since=`，与原先一致。
 */
export function useEventStream(sessionId: string | null, handlers: HandlerMap) {
  const lastSeqRef = useRef(0);
  const appliedSeqRef = useRef(0);
  /** 会话切换或 StrictMode 卸载时递增，丢弃上一 effect 未完成 bootstrap/startLive，避免回放写错 store / 污染 seq */
  const effectGenerationRef = useRef(0);
  const handlersRef = useRef(handlers);
  handlersRef.current = handlers;

  useEffect(() => {
    if (!sessionId) return;
    effectGenerationRef.current += 1;
    const generation = effectGenerationRef.current;
    const stale = () => generation !== effectGenerationRef.current;

    lastSeqRef.current = 0;
    appliedSeqRef.current = 0;

    let pollActive = true;
    let pollId: number | undefined;
    let es: EventSource | null = null;

    const applyRaw = async (raw: unknown) => {
      if (stale()) return;
      const seq = (raw as { seq?: unknown })?.seq;
      if (typeof seq === "number" && seq <= appliedSeqRef.current) return;
      await createDispatcher(handlersRef.current).handle(raw);
      if (typeof seq === "number") {
        appliedSeqRef.current = Math.max(appliedSeqRef.current, seq);
        lastSeqRef.current = appliedSeqRef.current;
      }
    };

    const startLive = () => {
      if (stale()) return;
      const since = appliedSeqRef.current;
      es = new EventSource(
        `/api/v1/sessions/${encodeURIComponent(sessionId)}/stream?since=${since}`,
      );

      const onMessage = (ev: MessageEvent) => {
        if (!ev.data || ev.data === "keep-alive") return;
        try {
          const raw = JSON.parse(ev.data);
          void applyRaw(raw);
        } catch {
          // swallow
        }
      };

      es.onmessage = onMessage;
      const eventTypes = [
        "message.delta",
        "thinking.delta",
        "tool.call",
        "tool.result",
        "screenshot",
        "artifact",
        "plan.update",
        "task.frame",
        "status",
        "token.usage",
        "interrupt.ack",
        "error",
        "ping",
      ];
      for (const t of eventTypes) {
        es.addEventListener(t, onMessage as EventListener);
      }

      es.onerror = () => {
        // EventSource will auto-reconnect
      };

      const poll = async () => {
        if (!pollActive || stale()) return;
        try {
          const batch = await listSessionEvents(sessionId, appliedSeqRef.current, 400);
          if (stale()) return;
          for (const ev of batch) {
            if (stale()) return;
            const seq = ev.seq;
            const raw = typeof seq === "number" ? { ...ev, seq } : ev;
            await applyRaw(raw);
          }
        } catch {
          // ignore
        }
      };
      void poll();
      pollId = window.setInterval(() => void poll(), POLL_MS);
    };

    const runBootstrap = async () => {
      let users: PersistedUserMessage[] = [];
      let eventPayloads: unknown[] = [];
      try {
        const [u, ev] = await Promise.all([
          listSessionMessages(sessionId),
          listAllSessionEventPayloads(sessionId),
        ]);
        if (stale()) return;
        users = u as PersistedUserMessage[];
        eventPayloads = ev;
      } catch {
        if (stale()) return;
        users = [];
        eventPayloads = [];
      }

      const merged = mergeUserMessagesAndEvents(users, eventPayloads);
      let maxSeq = 0;
      let yielded = 0;
      const yieldEvery = 32;
      /** 历史里每条 screenshot 都走 handler 会令 Dock/屏幕预览的 <img> 随 latest 连变，刷屏请求 artifacts/content */
      let lastScreenshot: unknown | null = null;

      for (const item of merged) {
        if (stale()) return;
        if (item.kind === "user") {
          useChatStore.getState().pushUserHydrated({
            id: item.id,
            text: item.text,
            createdAt: item.createdAt,
            attachments: item.attachments,
          });
          continue;
        }
        const raw = item.raw as { seq?: number; type?: string };
        if (typeof raw.seq === "number") {
          maxSeq = Math.max(maxSeq, raw.seq);
        }
        if (raw.type === "screenshot") {
          lastScreenshot = item.raw;
        } else {
          await createDispatcher(handlersRef.current).handle(item.raw);
        }
        yielded += 1;
        if (yielded % yieldEvery === 0) {
          await new Promise<void>((r) => setTimeout(r, 0));
          if (stale()) return;
        }
      }
      if (stale()) return;
      if (lastScreenshot) {
        await createDispatcher(handlersRef.current).handle(lastScreenshot);
      }
      if (maxSeq > 0) {
        appliedSeqRef.current = maxSeq;
        lastSeqRef.current = maxSeq;
      }
      startLive();
    };

    void runBootstrap();

    return () => {
      pollActive = false;
      effectGenerationRef.current += 1;
      if (pollId !== undefined) window.clearInterval(pollId);
      es?.close();
    };
  }, [sessionId]);

  return { lastSeq: () => lastSeqRef.current };
}

export { parseAgentEvent } from "@somna/event-schema";
