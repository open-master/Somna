import { parseAgentEvent, type AgentEvent } from "@somna/event-schema";

import { getResolvedAgentModels } from "@/lib/agent-models";
import { authHeaders } from "@/lib/auth/cookie";
import { getResolvedMcpToolModels } from "@/lib/mcp-tool-models";

/** 浏览器走 Next 反代，以便携带 cookie + Authorization；SSR 直连 agent-core（仅构建/少数场景）。 */
const API_BASE =
  typeof window !== "undefined"
    ? "/api/v1/sessions"
    : `${process.env.NEXT_PUBLIC_API_BASE || "http://127.0.0.1:8000"}/v1/sessions`;

export interface Session {
  id: string;
  user_id?: string;
  title: string;
  status: string;
  workflow_id?: string | null;
  run_id?: string | null;
  planner_model?: string | null;
  task_frame_model?: string | null;
  executor_model?: string | null;
  created_at?: string;
  updated_at?: string;
}

export type SessionEvent = AgentEvent & { seq?: number | null };

export async function listSessions(limit = 100): Promise<Session[]> {
  const res = await fetch(`${API_BASE}?limit=${limit}`, {
    cache: "no-store",
    headers: { ...authHeaders() },
  });
  if (res.status === 401) throw new Error("401");
  if (!res.ok) throw new Error(`listSessions: ${res.status}`);
  return res.json();
}

export async function createSession(title = "新会话"): Promise<Session> {
  const res = await fetch(API_BASE, {
    method: "POST",
    headers: { "content-type": "application/json", ...authHeaders() },
    body: JSON.stringify({ title }),
  });
  if (res.status === 401) throw new Error("401");
  if (!res.ok) throw new Error(`createSession: ${res.status}`);
  return res.json();
}

export async function getSession(id: string): Promise<Session> {
  const res = await fetch(`${API_BASE}/${id}`, {
    cache: "no-store",
    headers: { ...authHeaders() },
  });
  if (res.status === 401) throw new Error("401");
  if (!res.ok) throw new Error(`getSession: ${res.status}`);
  return res.json();
}

export async function patchSessionTitle(id: string, title: string): Promise<Session> {
  const res = await fetch(`${API_BASE}/${id}`, {
    method: "PATCH",
    headers: { "content-type": "application/json", ...authHeaders() },
    body: JSON.stringify({ title }),
  });
  if (res.status === 401) throw new Error("401");
  if (!res.ok) throw new Error(`patchSession: ${res.status}`);
  return res.json();
}

export async function deleteSession(id: string): Promise<void> {
  const res = await fetch(`${API_BASE}/${id}`, {
    method: "DELETE",
    headers: { ...authHeaders() },
  });
  if (res.status === 401) throw new Error("401");
  if (res.status === 409) {
    const t = await res.text();
    throw new Error(t || "delete conflict: session running");
  }
  if (!res.ok) throw new Error(`deleteSession: ${res.status}`);
}

export async function listSessionEvents(id: string, since = 0, limit = 200): Promise<SessionEvent[]> {
  const res = await fetch(`${API_BASE}/${id}/events?since=${since}&limit=${limit}`, {
    cache: "no-store",
    headers: { ...authHeaders() },
  });
  if (res.status === 401) throw new Error("401");
  if (!res.ok) throw new Error(`listSessionEvents: ${res.status}`);
  const payload = (await res.json()) as { events?: unknown[] };
  const events: SessionEvent[] = [];
  for (const raw of payload.events ?? []) {
    const parsed = parseSessionEvent(raw);
    if (parsed) events.push(parsed);
  }
  return events;
}

export async function postMessage(
  id: string,
  text: string,
  attachments: unknown[] = [],
  executor_engine: "native" | "anthropic" = "native",
): Promise<{ run_id: string; queued: boolean }> {
  const m = getResolvedAgentModels();
  const mcp = getResolvedMcpToolModels();
  const res = await fetch(`${API_BASE}/${id}/messages`, {
    method: "POST",
    headers: { "content-type": "application/json", ...authHeaders() },
    body: JSON.stringify({
      text,
      attachments,
      executor_engine,
      planner_model: m.planner,
      task_frame_model: m.taskframe,
      executor_model: m.executor,
      compact_model: m.cheap,
      coder_model: m.coder,
      reasoner_model: m.reasoner,
      longctx_model: m.longctx,
      mcp_tool_models: mcp,
    }),
  });
  if (res.status === 401) throw new Error("401");
  if (!res.ok) throw new Error(`postMessage: ${res.status}`);
  return res.json();
}

export async function interruptSession(
  id: string,
  reason: "user_interrupt" | "user_stop" = "user_interrupt",
): Promise<{ interrupted: boolean; run_id: string | null }> {
  const res = await fetch(`${API_BASE}/${id}/interrupt?reason=${encodeURIComponent(reason)}`, {
    method: "POST",
    headers: { ...authHeaders() },
  });
  if (res.status === 401) throw new Error("401");
  if (!res.ok) throw new Error(`interruptSession: ${res.status}`);
  return res.json();
}

/** 同源 EventSource：由 Next route 把 cookie 转为 upstream Authorization。 */
export function streamUrl(id: string, since = 0): string {
  return `/api/v1/sessions/${encodeURIComponent(id)}/stream?since=${since}`;
}

function parseSessionEvent(raw: unknown): SessionEvent | null {
  const parsed = parseAgentEvent(raw);
  if (!parsed) return null;
  const seq = typeof (raw as { seq?: unknown }).seq === "number" ? (raw as { seq: number }).seq : null;
  return { ...parsed, seq };
}
