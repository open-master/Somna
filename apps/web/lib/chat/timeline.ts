import type { SessionAttachmentRef } from "@/lib/api/sessions";

/** GET /sessions/{id}/messages 中单条用户消息形状 */
export type PersistedUserMessage = {
  id: string;
  content: { text?: string; attachments?: unknown[] };
  created_at: string;
};

function normalizeAttachments(raw: unknown): SessionAttachmentRef[] | undefined {
  if (!Array.isArray(raw) || raw.length === 0) return undefined;
  const out: SessionAttachmentRef[] = [];
  for (const a of raw) {
    if (!a || typeof a !== "object") continue;
    const o = a as Record<string, unknown>;
    if (typeof o.s3_key !== "string" || typeof o.id !== "string") continue;
    out.push({
      id: String(o.id),
      filename: typeof o.filename === "string" ? o.filename : "file",
      mime: typeof o.mime === "string" ? o.mime : "application/octet-stream",
      size: typeof o.size === "number" ? o.size : 0,
      s3_key: o.s3_key,
    });
  }
  return out.length ? out : undefined;
}

export function userTextFromContent(c: PersistedUserMessage["content"]): string {
  const t = (c.text ?? "").trim();
  if (t) return t;
  const atts = normalizeAttachments(c.attachments);
  if (atts?.length) return `「已添加 ${atts.length} 个附件」`;
  return "（空消息）";
}

export type MergedItem =
  | {
      kind: "user";
      id: string;
      text: string;
      createdAt: number;
      attachments?: SessionAttachmentRef[];
    }
  | { kind: "event"; raw: unknown };

/**
 * 将 DB 用户消息与 events 表回放事件按时间交错排序；
 * 同一时刻优先用户消息，再按事件 seq。
 */
export function mergeUserMessagesAndEvents(
  users: PersistedUserMessage[],
  eventPayloads: unknown[],
): MergedItem[] {
  const FALLBACK = Date.UTC(2020, 0, 1);
  const items: { sortT: number; order: number; payload: MergedItem }[] = [];
  let order = 0;
  for (const u of users) {
    const t = Date.parse(u.created_at);
    const sortT = Number.isFinite(t) ? t : FALLBACK;
    items.push({
      sortT,
      order: order++,
      payload: {
        kind: "user",
        id: u.id,
        text: userTextFromContent(u.content),
        createdAt: Number.isFinite(t) ? t : Date.now(),
        attachments: normalizeAttachments(u.content.attachments),
      },
    });
  }
  for (const raw of eventPayloads) {
    if (!raw || typeof raw !== "object") continue;
    const r = raw as Record<string, unknown>;
    const seq = typeof r.seq === "number" ? r.seq : 0;
    const ca = r.created_at;
    const parsed = typeof ca === "string" ? Date.parse(ca) : NaN;
    const sortT = Number.isFinite(parsed) ? parsed : FALLBACK + seq;
    items.push({
      sortT,
      order: order++,
      payload: { kind: "event", raw },
    });
  }
  items.sort((a, b) => {
    if (a.sortT !== b.sortT) return a.sortT - b.sortT;
    if (a.payload.kind !== b.payload.kind) {
      return a.payload.kind === "user" ? -1 : 1;
    }
    if (a.payload.kind === "event" && b.payload.kind === "event") {
      const sa = (a.payload.raw as { seq?: number }).seq ?? 0;
      const sb = (b.payload.raw as { seq?: number }).seq ?? 0;
      return sa - sb;
    }
    return a.order - b.order;
  });
  return items.map((x) => x.payload);
}
