/**
 * Event dispatcher — small helper for the frontend to route parsed AgentEvents
 * to per-slice handlers (zustand / redux / plain callbacks).
 *
 * Usage:
 *   const dispatcher = createDispatcher({
 *     "message.delta":  (e) => chatSlice.appendText(e),
 *     "tool.call":      (e) => chatSlice.addToolCall(e),
 *     // ...
 *   });
 *   for await (const raw of sseStream) dispatcher.handle(raw);
 */
import { parseAgentEvent, type AgentEvent, type AgentEventMap, type AgentEventType } from "./index";

export type HandlerMap = Partial<{
  [K in AgentEventType]: (event: AgentEventMap[K]) => void | Promise<void>;
}>;

export interface Dispatcher {
  handle(raw: unknown): Promise<void>;
  /** 同步派发；适用于 handler 无副作用 Promise 回放/批更新（handlers 若为 async 则被 fire-and-forget，避免 await 割裂 React 批处理）。 */
  handleSync(raw: unknown): void;
  on<K extends AgentEventType>(type: K, handler: (event: AgentEventMap[K]) => void | Promise<void>): void;
  off<K extends AgentEventType>(type: K): void;
}

export function createDispatcher(handlers: HandlerMap = {}): Dispatcher {
  const map: HandlerMap = { ...handlers };

  return {
    async handle(raw: unknown) {
      const parsed = parseAgentEvent(raw);
      if (!parsed) {
        if (typeof console !== "undefined") {
          console.warn("[somna-events] dropped unparseable event:", raw);
        }
        return;
      }
      const fn = (map as Record<string, ((e: AgentEvent) => unknown) | undefined>)[parsed.type];
      if (!fn) return;
      const result = fn(parsed);
      if (result != null && typeof (result as PromiseLike<unknown>).then === "function") {
        await result;
      }
    },
    handleSync(raw: unknown) {
      const parsed = parseAgentEvent(raw);
      if (!parsed) {
        if (typeof console !== "undefined") {
          console.warn("[somna-events] dropped unparseable event:", raw);
        }
        return;
      }
      const fn = (map as Record<string, ((e: AgentEvent) => unknown) | undefined>)[parsed.type];
      if (fn) void fn(parsed);
    },
    on(type, handler) {
      (map as Record<string, unknown>)[type] = handler as unknown;
    },
    off(type) {
      delete (map as Record<string, unknown>)[type];
    },
  };
}
