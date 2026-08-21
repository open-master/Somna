"use client";

import type { TaskFrameEvent } from "@somna/event-schema";
import { create } from "zustand";

type ClarificationQuestion = TaskFrameEvent["questions"][number];

interface TaskFrameState {
  summary: string | null;
  detail: string | null;
  questions: ClarificationQuestion[];
  runId: string | null;
  fromEvent: (e: TaskFrameEvent) => void;
  clear: () => void;
}

export const useTaskFrameStore = create<TaskFrameState>((set) => ({
  summary: null,
  detail: null,
  questions: [],
  runId: null,
  fromEvent: (e) =>
    set({
      summary: e.summary,
      detail: e.detail || null,
      questions: e.questions ?? [],
      runId: e.run_id ?? null,
    }),
  clear: () => set({ summary: null, detail: null, questions: [], runId: null }),
}));
