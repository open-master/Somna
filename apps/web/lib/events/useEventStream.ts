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
 * - **HTTP poll fallback**：轮询 `GET /events?since=`，与原先一致。
 */
export function useEventStream(sessionId: string | null, handlers: HandlerMap) {
  const lastSeqRef = useRef(0);
  const appliedSeqRef = useRef(0);
  const handlersRef = useRef(handlers);
  handlersRef.current = handlers;

  useEffect(() => {
    if (!sessionId) return;
    lastSeqRef.current = 0;
    appliedSeqRef.current = 0;

    let pollActive = true;
    let pollId: number | undefined;
    let es: EventSource | null = null;

    const applyRaw = async (raw: unknown) => {
      const seq = (raw as { seq?: unknown })?.seq;
      if (typeof seq === "number" && seq <= appliedSeqRef.current) return;
      await createDispatcher(handlersRef.current).handle(raw);
      if (typeof seq === "number") {
        appliedSeqRef.current = Math.max(appliedSeqRef.current, seq);
        lastSeqRef.current = appliedSeqRef.current;
      }
    };

    const startLive = () => {
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
        if (!pollActive) return;
        try {
          const batch = await listSessionEvents(sessionId, appliedSeqRef.current, 400);
          for (const ev of batch) {
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
        users = u as PersistedUserMessage[];
        eventPayloads = ev;
      } catch {
        users = [];
        eventPayloads = [];
      }

      const merged = mergeUserMessagesAndEvents(users, eventPayloads);
      let maxSeq = 0;
      let yielded = 0;
      const yieldEvery = 32;
      for (const item of merged) {
        if (item.kind === "user") {
          useChatStore.getState().pushUserHydrated({
            id: item.id,
            text: item.text,
            createdAt: item.createdAt,
            attachments: item.attachments,
          });
          continue;
        }
        const raw = item.raw as { seq?: number };
        await createDispatcher(handlersRef.current).handle(item.raw);
        if (typeof raw.seq === "number") {
          maxSeq = Math.max(maxSeq, raw.seq);
        }
        yielded += 1;
        if (yielded % yieldEvery === 0) {
          await new Promise<void>((r) => setTimeout(r, 0));
        }
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
      if (pollId !== undefined) window.clearInterval(pollId);
      es?.close();
    };
  }, [sessionId]);

  return { lastSeq: () => lastSeqRef.current };
}

export { parseAgentEvent } from "@somna/event-schema";
