import { create } from "zustand";
import type {
  ArtifactEvent,
  MessageDeltaEvent,
  ThinkingDeltaEvent,
  ToolCallEvent,
  ToolResultEvent,
} from "@somna/event-schema";

import type { SessionAttachmentRef } from "@/lib/api/sessions";

// Unified message model rendered by MessageList
export type ChatMessage =
  | {
      kind: "user";
      id: string;
      text: string;
      createdAt: number;
      attachments?: SessionAttachmentRef[];
    }
  | { kind: "assistant"; id: string; text: string; thinking?: string; createdAt: number }
  | { kind: "tool"; id: string; name: string; args: unknown; status: "running" | "ok" | "failed"; preview?: string; durationMs?: number; createdAt: number }
  | { kind: "artifact"; id: string; name: string; mime: string; url: string; createdAt: number };

interface ChatState {
  messages: ChatMessage[];
  activeAssistantId: string | null;
  pushUser: (text: string) => void;
  /** 自 DB 注水，id 与 createdAt 用服务端值，避免刷新后重复或乱序 */
  pushUserHydrated: (row: {
    id: string;
    text: string;
    createdAt: number;
    attachments?: SessionAttachmentRef[];
  }) => void;
  /** 新一轮 run 开始（如 status.planning）时切断上一轮助手气泡拼接 */
  beginAssistantTurn: () => void;
  /** postMessage 失败时撤销最后一条乐观插入的用户消息 */
  rollbackLastUserMessage: () => void;
  onMessageDelta: (e: MessageDeltaEvent) => void;
  onThinkingDelta: (e: ThinkingDeltaEvent) => void;
  onToolCall: (e: ToolCallEvent) => void;
  onToolResult: (e: ToolResultEvent) => void;
  onArtifact: (e: ArtifactEvent) => void;
  clear: () => void;
}

export const useChatStore = create<ChatState>((set) => ({
  messages: [],
  activeAssistantId: null,
  pushUser: (text) =>
    set((s) => ({
      messages: [
        ...s.messages,
        { kind: "user", id: `u_${Date.now()}`, text, createdAt: Date.now() },
      ],
    })),
  pushUserHydrated: (row) =>
    set((s) => ({
      messages: [
        ...s.messages,
        {
          kind: "user",
          id: row.id,
          text: row.text,
          createdAt: row.createdAt,
          ...(row.attachments?.length ? { attachments: row.attachments } : {}),
        },
      ],
    })),
  beginAssistantTurn: () => set({ activeAssistantId: null }),
  rollbackLastUserMessage: () =>
    set((s) => {
      const m = s.messages;
      if (m.length === 0) return s;
      const last = m[m.length - 1];
      if (last?.kind !== "user") return s;
      return { messages: m.slice(0, -1) };
    }),
  onMessageDelta: (e) =>
    set((s) => {
      const id = s.activeAssistantId ?? `a_${Date.now()}`;
      const existing = s.messages.find((m) => m.kind === "assistant" && m.id === id);
      if (existing && existing.kind === "assistant") {
        return {
          messages: s.messages.map((m) =>
            m === existing ? { ...existing, text: existing.text + e.text } : m,
          ),
          activeAssistantId: id,
        };
      }
      return {
        messages: [
          ...s.messages,
          { kind: "assistant", id, text: e.text, createdAt: Date.now() },
        ],
        activeAssistantId: id,
      };
    }),
  onThinkingDelta: (e) =>
    set((s) => {
      const id = s.activeAssistantId ?? `a_${Date.now()}`;
      const existing = s.messages.find((m) => m.kind === "assistant" && m.id === id);
      if (existing && existing.kind === "assistant") {
        return {
          messages: s.messages.map((m) =>
            m === existing ? { ...existing, thinking: (existing.thinking ?? "") + e.text } : m,
          ),
          activeAssistantId: id,
        };
      }
      return {
        messages: [
          ...s.messages,
          { kind: "assistant", id, text: "", thinking: e.text, createdAt: Date.now() },
        ],
        activeAssistantId: id,
      };
    }),
  onToolCall: (e) =>
    set((s) => ({
      messages: [
        ...s.messages,
        {
          kind: "tool",
          id: e.id,
          name: e.name,
          args: e.args,
          status: "running",
          createdAt: Date.now(),
        },
      ],
    })),
  onToolResult: (e) =>
    set((s) => ({
      messages: s.messages.map((m) =>
        m.kind === "tool" && m.id === e.id
          ? { ...m, status: e.ok ? "ok" : "failed", preview: e.preview, durationMs: e.duration_ms ?? undefined }
          : m,
      ),
    })),
  onArtifact: (e) =>
    set((s) => ({
      messages: [
        ...s.messages,
        {
          kind: "artifact",
          id: `art_${Date.now()}`,
          name: e.name,
          mime: e.mime,
          url: e.url,
          createdAt: Date.now(),
        },
      ],
    })),
  clear: () => set({ messages: [], activeAssistantId: null }),
}));
