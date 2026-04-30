import { create } from "zustand";
import type { SessionPhase } from "@somna/event-schema";

export interface SessionSummary {
  id: string;
  title: string;
  updatedAt: string;
  createdAt?: string;
  status?: string;
  runId?: string | null;
  workflowId?: string | null;
}

interface SessionState {
  currentId: string | null;
  phase: SessionPhase | "idle";
  runId: string | null;
  sessions: SessionSummary[];
  hydrateSessions: (sessions: SessionSummary[]) => void;
  setCurrent: (id: string | null) => void;
  setPhase: (phase: SessionPhase | "idle") => void;
  setRunId: (id: string | null) => void;
  upsertSession: (s: SessionSummary) => void;
  removeSession: (id: string) => void;
  resetRun: () => void;
}

export const useSessionStore = create<SessionState>((set) => ({
  currentId: null,
  phase: "idle",
  runId: null,
  sessions: [],
  hydrateSessions: (sessions) =>
    set(() => ({
      sessions: [...sessions].sort((a, b) => b.updatedAt.localeCompare(a.updatedAt)),
    })),
  setCurrent: (id) => set({ currentId: id }),
  setPhase: (phase) => set({ phase }),
  setRunId: (id) => set({ runId: id }),
  upsertSession: (s) =>
    set((state) => {
      const existing = state.sessions.find((x) => x.id === s.id);
      const merged = { ...existing, ...s };
      const without = state.sessions.filter((x) => x.id !== s.id);
      return {
        sessions: [merged, ...without].sort((a, b) => b.updatedAt.localeCompare(a.updatedAt)),
      };
    }),
  removeSession: (id) =>
    set((state) => ({ sessions: state.sessions.filter((x) => x.id !== id) })),
  resetRun: () => set({ phase: "idle", runId: null }),
}));
