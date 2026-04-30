import { create } from "zustand";
import type { PlanUpdateEvent, TodoItem } from "@somna/event-schema";

interface PlanState {
  todos: TodoItem[];
  onPlanUpdate: (e: PlanUpdateEvent) => void;
  clear: () => void;
}

export const usePlanStore = create<PlanState>((set) => ({
  todos: [],
  onPlanUpdate: (e) => set({ todos: e.todos }),
  clear: () => set({ todos: [] }),
}));
