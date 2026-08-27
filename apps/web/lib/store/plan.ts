import { create } from "zustand";
import type { PlanUpdateEvent, TodoItem } from "@somna/event-schema";

interface PlanState {
  todos: TodoItem[];
  runId: string | null;
  planId: string | null;
  planVersion: number;
  onRunStarted: (runId: string | null) => void;
  onPlanUpdate: (e: PlanUpdateEvent) => void;
  clear: () => void;
}

export const usePlanStore = create<PlanState>((set) => ({
  todos: [],
  runId: null,
  planId: null,
  planVersion: 0,
  onRunStarted: (runId) =>
    set((state) =>
      state.runId === runId
        ? state
        : { todos: [], runId, planId: null, planVersion: 0 },
    ),
  onPlanUpdate: (e) =>
    set((state) => {
      const eventRunId = e.run_id ?? null;
      if (state.runId && eventRunId && state.runId !== eventRunId) return state;
      return {
        todos: e.todos,
        runId: eventRunId ?? state.runId,
        planId: e.plan_id ?? null,
        planVersion: e.plan_version ?? 1,
      };
    }),
  clear: () => set({ todos: [], runId: null, planId: null, planVersion: 0 }),
}));
