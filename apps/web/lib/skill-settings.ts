"use client";

export type SkillMode = "auto" | "off";

const STORAGE_KEY = "somna_skill_mode";
const STORAGE_KEY_SHOW_SKILL_DEBUG = "somna_show_skill_debug_in_chat";
const SHOW_SKILL_DEBUG_CHANGED = "somna:show-skill-debug-in-chat-changed";

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

/** 是否在聊天顶栏展示 Skill 匹配信息（仅前端 UI，与任务是否启用 Skill 无关）。默认展示。 */
export function getShowSkillDebugInChat(): boolean {
  if (typeof window === "undefined") return true;
  const raw = window.localStorage.getItem(STORAGE_KEY_SHOW_SKILL_DEBUG);
  if (raw === null) return true;
  return raw !== "0";
}

export function setShowSkillDebugInChat(show: boolean): void {
  if (typeof window === "undefined") return;
  if (show) {
    window.localStorage.removeItem(STORAGE_KEY_SHOW_SKILL_DEBUG);
  } else {
    window.localStorage.setItem(STORAGE_KEY_SHOW_SKILL_DEBUG, "0");
  }
  window.dispatchEvent(new CustomEvent(SHOW_SKILL_DEBUG_CHANGED));
}

export function subscribeShowSkillDebugInChat(listener: () => void): () => void {
  if (typeof window === "undefined") return () => {};
  const onCustom = () => listener();
  const onStorage = (e: StorageEvent) => {
    if (e.key === STORAGE_KEY_SHOW_SKILL_DEBUG || e.key === null) listener();
  };
  window.addEventListener(SHOW_SKILL_DEBUG_CHANGED, onCustom);
  window.addEventListener("storage", onStorage);
  return () => {
    window.removeEventListener(SHOW_SKILL_DEBUG_CHANGED, onCustom);
    window.removeEventListener("storage", onStorage);
  };
}
