"use client";
import { useEffect, useRef } from "react";
import { parseAgentEvent } from "@somna/event-schema";
import { createDispatcher } from "@somna/event-schema/dispatcher";
import type { HandlerMap } from "@somna/event-schema/dispatcher";

import { listSessionEvents, streamUrl } from "@/lib/api/sessions";

const POLL_MS = 1_500;

/**
 * Subscribe to a session's SSE stream.
 *
 * - Reconnects automatically via EventSource's native behavior
 * - Passes each event through the zod-based dispatcher
 * - Server sends event type in the SSE `event:` header and JSON in `data:`
 * - **HTTP poll fallback**: some browsers / proxies mishandle SSE; we periodically
 *   `GET /events?since=` so the UI still streams. Deduped by monotonic `seq`.
 */
export function useEventStream(sessionId: string | null, handlers: HandlerMap) {
  const lastSeqRef = useRef(0);
  /** Highest `seq` applied to handlers (SSE or poll). */
  const appliedSeqRef = useRef(0);
  const handlersRef = useRef(handlers);
  handlersRef.current = handlers;

  useEffect(() => {
    if (!sessionId) return;
    // `events.id` is global; switching sessions must not reuse the previous high-water mark.
    lastSeqRef.current = 0;
    appliedSeqRef.current = 0;

    const applyRaw = async (raw: unknown) => {
      const seq = (raw as { seq?: unknown })?.seq;
      if (typeof seq === "number" && seq <= appliedSeqRef.current) return;
      await createDispatcher(handlersRef.current).handle(raw);
      if (typeof seq === "number") {
        appliedSeqRef.current = Math.max(appliedSeqRef.current, seq);
        lastSeqRef.current = appliedSeqRef.current;
      }
    };

    const es = new EventSource(streamUrl(sessionId, lastSeqRef.current));

    const onMessage = (ev: MessageEvent) => {
      if (!ev.data || ev.data === "keep-alive") return;
      try {
        const raw = JSON.parse(ev.data);
        void applyRaw(raw);
      } catch {
        // swallow; dispatcher also logs unparseable events
      }
    };

    // Generic 'message' listener — covers the case where server didn't set event name
    es.onmessage = onMessage;

    // Typed listeners — server sets `event: <type>` so browsers won't hit onmessage
    const eventTypes = [
      "message.delta",
      "thinking.delta",
      "tool.call",
      "tool.result",
      "screenshot",
      "artifact",
      "plan.update",
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
      // EventSource will auto-reconnect; nothing to do here.
    };

    let pollActive = true;
    const poll = async () => {
      if (!pollActive) return;
      try {
        const batch = await listSessionEvents(sessionId, appliedSeqRef.current, 400);
        for (const ev of batch) {
          const seq = ev.seq;
          const raw =
            typeof seq === "number"
              ? { ...ev, seq }
              : ev;
          await applyRaw(raw);
        }
      } catch {
        // ignore transient network errors
      }
    };
    void poll();
    const pollId = window.setInterval(() => void poll(), POLL_MS);

    return () => {
      pollActive = false;
      window.clearInterval(pollId);
      es.close();
    };
  }, [sessionId]);

  return { lastSeq: () => lastSeqRef.current };
}

// Re-export for convenience so callers need only one import.
export { parseAgentEvent };
