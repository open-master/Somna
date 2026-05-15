"use client";

import type { SkillDebugEvent, SkillDebugItem } from "@somna/event-schema";
import { create } from "zustand";

interface SkillDebugState {
  candidateCount: number | null;
  selectedSkills: SkillDebugItem[];
  runId: string | null;
  fromEvent: (e: SkillDebugEvent) => void;
  clear: () => void;
}

export const useSkillDebugStore = create<SkillDebugState>((set) => ({
  candidateCount: null,
  selectedSkills: [],
  runId: null,
  fromEvent: (e) =>
    set({
      candidateCount: e.candidate_count,
      selectedSkills: e.selected_skills,
      runId: e.run_id ?? null,
    }),
  clear: () => set({ candidateCount: null, selectedSkills: [], runId: null }),
}));
