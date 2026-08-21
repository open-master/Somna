"use client";

import type { Session } from "@/lib/api/sessions";
import { createSession } from "@/lib/api/sessions";
import { useSessionStore } from "@/lib/store/session";
import type { SessionSummary } from "@/lib/store/session";

function toSummary(s: Session): SessionSummary {
  return {
    id: s.id,
    title: s.title,
    status: s.status,
    runId: s.run_id ?? null,
    workflowId: s.workflow_id ?? null,
    createdAt: s.created_at,
    updatedAt: s.updated_at ?? new Date().toISOString(),
  };
}

type AppRouter = { replace: (href: string) => void; refresh: () => void };

/** 登录接口已通过 Set-Cookie 写入 HttpOnly JWT 后再跳转。 */
export async function finishLogin(
  router: AppRouter,
  nextPath: string,
  afterLogin: string | null,
): Promise<void> {
  if (afterLogin === "new-session") {
    try {
      const s = await createSession();
      useSessionStore.getState().upsertSession(toSummary(s));
      router.replace(`/chat/${s.id}`);
    } catch {
      router.replace(nextPath);
    }
    router.refresh();
    return;
  }
  router.replace(nextPath);
  router.refresh();
}

export function buildAuthHref(
  base: "/login" | "/register",
  opts: { next?: string; afterLogin?: string | null },
): string {
  const q = new URLSearchParams();
  if (opts.next && opts.next !== "/") q.set("next", opts.next);
  if (opts.afterLogin) q.set("afterLogin", opts.afterLogin);
  const s = q.toString();
  return s ? `${base}?${s}` : base;
}
