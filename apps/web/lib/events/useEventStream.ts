"use client";
import { startTransition, useEffect, useRef } from "react";
import { unstable_batchedUpdates as batchedUpdates } from "react-dom";
import { createDispatcher } from "@somna/event-schema/dispatcher";
import type { HandlerMap } from "@somna/event-schema/dispatcher";

import { mergeUserMessagesAndEvents, type MergedItem, type PersistedUserMessage } from "@/lib/chat/timeline";
import { listAllSessionEventPayloads, listSessionEvents, listSessionMessages } from "@/lib/api/sessions";
import { useChatStore } from "@/lib/store/chat";

const POLL_MS = 1_500;

/**
 * Subscribe to a session's SSE stream.
 *
 * - **Bootstrap**：拉取 DB 用户消息 + 全量 events，按 `created_at` 交错回放后，再以最大 seq 连接 SSE，避免与库表重复。
 *   （弱 C：`startTransition` + `batchedUpdates` 按块回放，仍为每条事件调用同一 handlers，仅合并提交减少中间帧。）
 *   （回放时暂不逐条应用 `screenshot`，只在结束时应用最后一张，以免观察区预览图连环换 src 刷屏拉取 artifacts。）
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

    const applyRaw = (raw: unknown) => {
      if (stale()) return;
      const seq = (raw as { seq?: unknown })?.seq;
      if (typeof seq === "number" && seq <= appliedSeqRef.current) return;
      batchedUpdates(() => {
        if (stale()) return;
        createDispatcher(handlersRef.current).handleSync(raw);
      });
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
          batchedUpdates(() => {
            if (stale()) return;
            const d = createDispatcher(handlersRef.current);
            for (const ev of batch) {
              if (stale()) return;
              const seq = ev.seq;
              const raw = typeof seq === "number" ? { ...ev, seq } : ev;
              const q = (raw as { seq?: unknown }).seq;
              if (typeof q === "number" && q <= appliedSeqRef.current) continue;
              d.handleSync(raw);
              if (typeof q === "number") {
                appliedSeqRef.current = Math.max(appliedSeqRef.current, q);
                lastSeqRef.current = appliedSeqRef.current;
              }
            }
          });
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
      /** 与同 tick 一并 `batchedUpdates` 的用户消息与事件回放项 */
      const chunk: MergedItem[] = [];

      const replayChunk = (items: MergedItem[]) => {
        if (items.length === 0) return;
        startTransition(() => {
          batchedUpdates(() => {
            if (stale()) return;
            const d = createDispatcher(handlersRef.current);
            for (const mi of items) {
              if (mi.kind === "user") {
                useChatStore.getState().pushUserHydrated({
                  id: mi.id,
                  text: mi.text,
                  createdAt: mi.createdAt,
                  attachments: mi.attachments,
                });
              } else {
                d.handleSync(mi.raw);
              }
            }
          });
        });
      };

      for (const item of merged) {
        if (stale()) return;
        if (item.kind === "user") {
          chunk.push(item);
        } else {
          const raw = item.raw as { seq?: number; type?: string };
          if (typeof raw.seq === "number") {
            maxSeq = Math.max(maxSeq, raw.seq);
          }
          if (raw.type === "screenshot") {
            lastScreenshot = item.raw;
          } else {
            chunk.push(item);
          }
        }
        yielded += 1;
        if (yielded % yieldEvery === 0) {
          replayChunk(chunk.splice(0, chunk.length));
          await new Promise<void>((r) => setTimeout(r, 0));
          if (stale()) return;
        }
      }
      if (stale()) return;
      replayChunk(chunk.splice(0, chunk.length));
      if (stale()) return;
      const screenshotToApply = lastScreenshot;
      if (screenshotToApply) {
        startTransition(() => {
          batchedUpdates(() => {
            if (stale()) return;
            createDispatcher(handlersRef.current).handleSync(screenshotToApply);
          });
        });
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
