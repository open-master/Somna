"use client";

import type { TaskFrameEvent } from "@somna/event-schema";
import { create } from "zustand";

interface TaskFrameState {
  summary: string | null;
  detail: string | null;
  runId: string | null;
  fromEvent: (e: TaskFrameEvent) => void;
  clear: () => void;
}

export const useTaskFrameStore = create<TaskFrameState>((set) => ({
  summary: null,
  detail: null,
  runId: null,
  fromEvent: (e) =>
    set({
      summary: e.summary,
      detail: e.detail || null,
      runId: e.run_id ?? null,
    }),
  clear: () => set({ summary: null, detail: null, runId: null }),
}));
