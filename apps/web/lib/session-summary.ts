import type { Session } from "@/lib/api/sessions";
import type { LastRunTerminal, SessionSummary } from "@/lib/store/session";

export function lastRunTerminalFromPhase(
  phase: string | null | undefined,
): LastRunTerminal | null {
  if (phase === "done") return "success";
  if (phase === "partial") return "partial";
  if (phase === "error") return "error";
  return null;
}

export function sessionToSummary(session: Session): SessionSummary {
  const lastPhase = session.last_phase ?? null;
  return {
    id: session.id,
    title: session.title,
    status: session.status,
    runId: session.run_id ?? null,
    workflowId: session.workflow_id ?? null,
    createdAt: session.created_at,
    updatedAt: session.updated_at ?? new Date().toISOString(),
    lastPhase,
    lastRunTerminal: lastRunTerminalFromPhase(lastPhase),
    awaitingUser: lastPhase === "waiting_user",
  };
}
