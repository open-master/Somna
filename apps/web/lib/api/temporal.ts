import { authHeaders } from "@/lib/auth/cookie";

const API_BASE =
  typeof window !== "undefined" ? "/api/v1/temporal" : `${process.env.NEXT_PUBLIC_API_BASE || "http://127.0.0.1:8000"}/v1/temporal`;

export interface LinkedSession {
  id: string;
  title: string;
  status: string;
  workflow_id?: string | null;
  run_id?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface TemporalWorkflow {
  workflow_id: string;
  run_id?: string | null;
  status?: string | null;
  workflow_type?: string | null;
  task_queue?: string | null;
  start_time?: string | null;
  close_time?: string | null;
  execution_time?: string | null;
  history_length?: number | null;
  namespace?: string | null;
  parent_id?: string | null;
  parent_run_id?: string | null;
  root_id?: string | null;
  root_run_id?: string | null;
  search_attributes?: unknown;
  session?: LinkedSession | null;
}

export interface TemporalHistoryEvent {
  event_id?: string | number | null;
  event_type?: string | null;
  event_time?: string | null;
  summary?: string | null;
  reason?: string | null;
  retry_state?: string | null;
  new_execution_run_id?: string | null;
  failure_message?: string | null;
  failure_chain?: string[];
  details?: Record<string, unknown>;
}

export interface TemporalWorkflowDetail {
  workflow: TemporalWorkflow;
  session?: LinkedSession | null;
  history: TemporalHistoryEvent[];
}

export interface TemporalWorkflowActionResp {
  ok: boolean;
  action: string;
  workflow_id: string;
  run_id?: string | null;
  session_id?: string | null;
}

export async function listTemporalWorkflows(
  limit = 100,
  query = "WorkflowType='SessionRunWorkflow'",
): Promise<{ query: string; workflows: TemporalWorkflow[] }> {
  const res = await fetch(
    `${API_BASE}/workflows?limit=${limit}&query=${encodeURIComponent(query)}`,
    { cache: "no-store", headers: { ...authHeaders() } },
  );
  if (!res.ok) throw new Error(`listTemporalWorkflows: ${res.status}`);
  return res.json();
}

export async function getTemporalWorkflow(
  workflowId: string,
  opts: { runId?: string | null; historyLimit?: number } = {},
): Promise<TemporalWorkflowDetail> {
  const params = new URLSearchParams();
  if (opts.runId) params.set("run_id", opts.runId);
  params.set("history_limit", String(opts.historyLimit ?? 100));
  const res = await fetch(`${API_BASE}/workflows/${encodeURIComponent(workflowId)}?${params.toString()}`, {
    cache: "no-store",
    headers: { ...authHeaders() },
  });
  if (!res.ok) throw new Error(`getTemporalWorkflow: ${res.status}`);
  return res.json();
}

export async function cancelTemporalWorkflow(
  workflowId: string,
  opts: { runId?: string | null; reason?: string } = {},
): Promise<TemporalWorkflowActionResp> {
  const params = new URLSearchParams();
  if (opts.runId) params.set("run_id", opts.runId);
  params.set("reason", opts.reason ?? "user_interrupt");
  const res = await fetch(`${API_BASE}/workflows/${encodeURIComponent(workflowId)}/cancel?${params.toString()}`, {
    method: "POST",
    headers: { ...authHeaders() },
  });
  if (!res.ok) throw new Error(`cancelTemporalWorkflow: ${res.status}`);
  return res.json();
}

export async function terminateTemporalWorkflow(
  workflowId: string,
  opts: { runId?: string | null; reason?: string } = {},
): Promise<TemporalWorkflowActionResp> {
  const params = new URLSearchParams();
  if (opts.runId) params.set("run_id", opts.runId);
  params.set("reason", opts.reason ?? "user_stop");
  const res = await fetch(`${API_BASE}/workflows/${encodeURIComponent(workflowId)}/terminate?${params.toString()}`, {
    method: "POST",
    headers: { ...authHeaders() },
  });
  if (!res.ok) throw new Error(`terminateTemporalWorkflow: ${res.status}`);
  return res.json();
}

export async function retryTemporalWorkflow(workflowId: string): Promise<TemporalWorkflowActionResp> {
  const res = await fetch(`${API_BASE}/workflows/${encodeURIComponent(workflowId)}/retry`, {
    method: "POST",
    headers: { ...authHeaders() },
  });
  if (!res.ok) throw new Error(`retryTemporalWorkflow: ${res.status}`);
  return res.json();
}
