import { create } from "zustand";
import type {
  AgentEvent,
  ArtifactEvent,
  ScreenshotEvent,
  TokenUsageEvent,
  ToolCallEvent,
  ToolResultEvent,
} from "@somna/event-schema";

interface Shot {
  ts: number;
  url: string;
  source: "browser" | "desktop" | "custom";
}

interface Usage {
  input: number;
  output: number;
  cost: number;
  byModel: Record<string, { input: number; output: number; cost: number }>;
}

interface ArtifactItem {
  id: string;
  name: string;
  mime: string;
  url: string;
  description?: string;
  ts: number;
}

interface FileItem {
  path: string;
  source: "artifact" | "tool";
  ts: number;
}

interface RecentEvent {
  type: string;
  seq?: number | null;
  summary: string;
  ts: number;
}

interface LiveState {
  screenshots: Shot[];
  latestScreenshot: Shot | null;
  artifacts: ArtifactItem[];
  fileItems: FileItem[];
  usage: Usage;
  terminalLines: string[];
  recentEvents: RecentEvent[];
  onScreenshot: (e: ScreenshotEvent) => void;
  onArtifact: (e: ArtifactEvent) => void;
  onUsage: (e: TokenUsageEvent) => void;
  onToolCall: (e: ToolCallEvent) => void;
  onToolResult: (e: ToolResultEvent) => void;
  track: (e: AgentEvent) => void;
  clear: () => void;
}

const emptyUsage: Usage = { input: 0, output: 0, cost: 0, byModel: {} };

const MAX_TERM = 500;
const MAX_EVENTS = 200;

export const useLiveStore = create<LiveState>((set) => ({
  screenshots: [],
  latestScreenshot: null,
  artifacts: [],
  fileItems: [],
  usage: emptyUsage,
  terminalLines: [],
  recentEvents: [],
  onScreenshot: (e) =>
    set((s) => {
      const shot: Shot = {
        ts: new Date(e.ts ?? Date.now()).getTime(),
        url: e.url,
        source: e.source,
      };
      return { screenshots: [...s.screenshots, shot], latestScreenshot: shot };
    }),
  onArtifact: (e) =>
    set((s) => {
      const artifact: ArtifactItem = {
        id: `${e.name}-${Date.now()}`,
        name: e.name,
        mime: e.mime,
        url: e.url,
        description: e.description ?? undefined,
        ts: new Date(e.ts ?? Date.now()).getTime(),
      };
      const filePath = e.description?.trim();
      const nextFiles = filePath
        ? upsertFileItem(s.fileItems, {
            path: filePath,
            source: "artifact",
            ts: artifact.ts,
          })
        : s.fileItems;
      return {
        artifacts: [artifact, ...s.artifacts].slice(0, 100),
        fileItems: nextFiles,
      };
    }),
  onUsage: (e) =>
    set((s) => {
      const prev = s.usage.byModel[e.model] ?? { input: 0, output: 0, cost: 0 };
      const next = {
        input: prev.input + e.input,
        output: prev.output + e.output,
        cost: prev.cost + (e.cost_usd ?? 0),
      };
      return {
        usage: {
          input: s.usage.input + e.input,
          output: s.usage.output + e.output,
          cost: s.usage.cost + (e.cost_usd ?? 0),
          byModel: { ...s.usage.byModel, [e.model]: next },
        },
      };
    }),
  onToolCall: (e) =>
    set((s) => {
      if (e.name !== "shell") return s;
      const cmd = typeof e.args === "object" && e.args
        ? ((e.args as Record<string, unknown>).cmd as string) ?? JSON.stringify(e.args)
        : String(e.args);
      const line = `$ ${cmd}`;
      const next = [...s.terminalLines, line].slice(-MAX_TERM);
      return {
        terminalLines: next,
        fileItems: collectToolFileItems(s.fileItems, e),
      };
    }),
  onToolResult: (e) =>
    set((s) => {
      if (!e.preview) return s;
      const lines = [...s.terminalLines, e.preview].slice(-MAX_TERM);
      return { terminalLines: lines };
    }),
  track: (e) =>
    set((s) => {
      const rec: RecentEvent = {
        type: e.type,
        seq: (e as { seq?: number | null }).seq ?? null,
        summary: summarize(e),
        ts: Date.now(),
      };
      return { recentEvents: [rec, ...s.recentEvents].slice(0, MAX_EVENTS) };
    }),
  clear: () =>
    set({
      screenshots: [],
      latestScreenshot: null,
      artifacts: [],
      fileItems: [],
      usage: emptyUsage,
      terminalLines: [],
      recentEvents: [],
    }),
}));

function summarize(e: AgentEvent): string {
  switch (e.type) {
    case "message.delta":
      return e.text.slice(0, 80);
    case "thinking.delta":
      return `(thinking) ${e.text.slice(0, 60)}`;
    case "tool.call":
      return `${e.name}(${JSON.stringify(e.args).slice(0, 60)})`;
    case "tool.result":
      return `${e.ok ? "ok" : "fail"} ${e.preview.slice(0, 60)}`;
    case "screenshot":
      return `${e.source} ${e.url}`;
    case "artifact":
      return `${e.name} (${e.mime})`;
    case "plan.update":
      return `${e.todos.length} todos`;
    case "status":
      return `${e.phase}${e.message ? ": " + e.message : ""}`;
    case "token.usage":
      return `${e.model} ${e.input}/${e.output} $${e.cost_usd.toFixed(4)}`;
    case "interrupt.ack":
      return e.reason;
    case "error":
      return `[${e.code}] ${e.message}`;
    default:
      return "";
  }
}

function upsertFileItem(items: FileItem[], next: FileItem): FileItem[] {
  const filtered = items.filter((item) => item.path !== next.path);
  return [next, ...filtered].slice(0, 200);
}

function collectToolFileItems(items: FileItem[], e: ToolCallEvent): FileItem[] {
  const args = (e.args ?? {}) as Record<string, unknown>;
  const ts = Date.now();
  if (e.name === "filesystem") {
    const path = typeof args.path === "string" ? args.path : null;
    if (!path) return items;
    return upsertFileItem(items, { path, source: "tool", ts });
  }
  if (e.name !== "shell") return items;
  const cmd = typeof args.cmd === "string" ? args.cmd : "";
  const matches = Array.from(cmd.matchAll(/(?:^|\s)(\.\/?[^\s;|&]+|\/[^\s;|&]+\.[^\s;|&]+)/g));
  return matches.reduce<FileItem[]>(
    (acc, match) => (match[1] ? upsertFileItem(acc, { path: match[1], source: "tool", ts }) : acc),
    items,
  );
}
