/**
 * Somna AI — Agent → UI event protocol (zod mirror of the Pydantic SSOT).
 *
 * Keep this file in sync with `packages/event-schema/python/somna_events/events.py`.
 * The matching tests and JSON schema under `schema/` guard against drift.
 */
import { z } from "zod";

export const AGENT_EVENT_SCHEMA_VERSION = "1";

// =============================================================
// Enums
// =============================================================
export const sessionPhaseSchema = z.enum([
  "idle",
  "planning",
  "executing",
  "compacting",
  "waiting_user",
  "interrupted",
  "stopped",
  "done",
  "error",
]);
export type SessionPhase = z.infer<typeof sessionPhaseSchema>;

export const todoStatusSchema = z.enum([
  "pending",
  "in_progress",
  "done",
  "failed",
  "skipped",
]);
export type TodoStatus = z.infer<typeof todoStatusSchema>;

export const screenshotSourceSchema = z.enum(["browser", "desktop", "custom"]);
export type ScreenshotSource = z.infer<typeof screenshotSourceSchema>;

// =============================================================
// Nested value objects
// =============================================================
export const todoItemSchema = z.object({
  id: z.string(),
  text: z.string(),
  status: todoStatusSchema,
  parent_id: z.string().nullable().optional(),
});
export type TodoItem = z.infer<typeof todoItemSchema>;

// =============================================================
// Base + concrete events
// =============================================================
const baseFields = {
  v: z.string().default(AGENT_EVENT_SCHEMA_VERSION),
  session_id: z.string().uuid(),
  run_id: z.string().nullable().optional(),
  seq: z.number().int().nullable().optional(),
  ts: z.string().datetime({ offset: true }).optional(),
};

export const messageDeltaSchema = z
  .object({
    type: z.literal("message.delta"),
    role: z.literal("assistant").default("assistant"),
    text: z.string(),
    ...baseFields,
  })
  .passthrough();

export const thinkingDeltaSchema = z
  .object({
    type: z.literal("thinking.delta"),
    text: z.string(),
    ...baseFields,
  })
  .passthrough();

export const toolCallSchema = z
  .object({
    type: z.literal("tool.call"),
    id: z.string(),
    name: z.string(),
    args: z.unknown(),
    ...baseFields,
  })
  .passthrough();

export const toolResultSchema = z
  .object({
    type: z.literal("tool.result"),
    id: z.string(),
    ok: z.boolean(),
    preview: z.string().default(""),
    full_ref: z.string().nullable().optional(),
    duration_ms: z.number().int().nullable().optional(),
    ...baseFields,
  })
  .passthrough();

export const screenshotSchema = z
  .object({
    type: z.literal("screenshot"),
    url: z.string(),
    source: screenshotSourceSchema.default("browser"),
    width: z.number().int().nullable().optional(),
    height: z.number().int().nullable().optional(),
    ...baseFields,
  })
  .passthrough();

export const artifactSchema = z
  .object({
    type: z.literal("artifact"),
    name: z.string(),
    mime: z.string(),
    url: z.string(),
    size_bytes: z.number().int().nullable().optional(),
    description: z.string().nullable().optional(),
    ...baseFields,
  })
  .passthrough();

export const planUpdateSchema = z
  .object({
    type: z.literal("plan.update"),
    todos: z.array(todoItemSchema),
    ...baseFields,
  })
  .passthrough();

export const taskFrameSchema = z
  .object({
    type: z.literal("task.frame"),
    summary: z.string(),
    detail: z.string().default(""),
    ...baseFields,
  })
  .passthrough();

export const statusSchema = z
  .object({
    type: z.literal("status"),
    phase: sessionPhaseSchema,
    message: z.string().nullable().optional(),
    ...baseFields,
  })
  .passthrough();

export const tokenUsageSchema = z
  .object({
    type: z.literal("token.usage"),
    model: z.string(),
    input: z.number().int(),
    output: z.number().int(),
    cost_usd: z.number().default(0),
    ...baseFields,
  })
  .passthrough();

export const interruptAckSchema = z
  .object({
    type: z.literal("interrupt.ack"),
    reason: z.string(),
    ...baseFields,
  })
  .passthrough();

export const errorEventSchema = z
  .object({
    type: z.literal("error"),
    code: z.string(),
    message: z.string(),
    retryable: z.boolean().default(false),
    ...baseFields,
  })
  .passthrough();

// =============================================================
// Discriminated union
// =============================================================
export const agentEventSchema = z.discriminatedUnion("type", [
  messageDeltaSchema,
  thinkingDeltaSchema,
  toolCallSchema,
  toolResultSchema,
  screenshotSchema,
  artifactSchema,
  planUpdateSchema,
  taskFrameSchema,
  statusSchema,
  tokenUsageSchema,
  interruptAckSchema,
  errorEventSchema,
]);
export type AgentEvent = z.infer<typeof agentEventSchema>;

export type MessageDeltaEvent = z.infer<typeof messageDeltaSchema>;
export type ThinkingDeltaEvent = z.infer<typeof thinkingDeltaSchema>;
export type ToolCallEvent = z.infer<typeof toolCallSchema>;
export type ToolResultEvent = z.infer<typeof toolResultSchema>;
export type ScreenshotEvent = z.infer<typeof screenshotSchema>;
export type ArtifactEvent = z.infer<typeof artifactSchema>;
export type PlanUpdateEvent = z.infer<typeof planUpdateSchema>;
export type TaskFrameEvent = z.infer<typeof taskFrameSchema>;
export type StatusEvent = z.infer<typeof statusSchema>;
export type TokenUsageEvent = z.infer<typeof tokenUsageSchema>;
export type InterruptAckEvent = z.infer<typeof interruptAckSchema>;
export type ErrorEvent = z.infer<typeof errorEventSchema>;

// Type → event map (for the dispatcher)
export type AgentEventMap = {
  "message.delta": MessageDeltaEvent;
  "thinking.delta": ThinkingDeltaEvent;
  "tool.call": ToolCallEvent;
  "tool.result": ToolResultEvent;
  screenshot: ScreenshotEvent;
  artifact: ArtifactEvent;
  "plan.update": PlanUpdateEvent;
  "task.frame": TaskFrameEvent;
  status: StatusEvent;
  "token.usage": TokenUsageEvent;
  "interrupt.ack": InterruptAckEvent;
  error: ErrorEvent;
};

export type AgentEventType = keyof AgentEventMap;

/**
 * Safe parse a raw JSON payload into an AgentEvent.
 */
export function parseAgentEvent(raw: unknown): AgentEvent | null {
  const result = agentEventSchema.safeParse(raw);
  return result.success ? result.data : null;
}

/**
 * Envelope used over NATS / SSE / WS.
 */
export const eventEnvelopeSchema = z.object({
  subject: z.string(),
  event: agentEventSchema,
});
export type EventEnvelope = z.infer<typeof eventEnvelopeSchema>;
