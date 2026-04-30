export type ExecutorEngine = "native" | "anthropic";

const STORAGE_KEY = "somna_executor_engine";

export function getExecutorEngine(): ExecutorEngine {
  if (typeof window === "undefined") return "native";
  const raw = window.localStorage.getItem(STORAGE_KEY);
  if (raw === "anthropic") return "anthropic";
  return "native";
}

export function setExecutorEngine(mode: ExecutorEngine): void {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(STORAGE_KEY, mode);
}

export function executorEngineLabel(mode: ExecutorEngine): string {
  return mode === "anthropic" ? "模式二 · Anthropic 协议" : "模式一 · OpenAI 协议";
}
