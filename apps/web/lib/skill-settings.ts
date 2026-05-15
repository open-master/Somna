"use client";

export type SkillMode = "auto" | "off";

const STORAGE_KEY = "somna_skill_mode";

export function getSkillMode(): SkillMode {
  if (typeof window === "undefined") return "auto";
  const raw = window.localStorage.getItem(STORAGE_KEY);
  return raw === "off" ? "off" : "auto";
}

export function setSkillMode(mode: SkillMode): void {
  if (typeof window === "undefined") return;
  if (mode === "auto") {
    window.localStorage.removeItem(STORAGE_KEY);
  } else {
    window.localStorage.setItem(STORAGE_KEY, mode);
  }
}
