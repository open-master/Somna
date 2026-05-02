"use client";

import type { Session } from "@/lib/api/sessions";
import { createSession } from "@/lib/api/sessions";
import { setAccessTokenCookie } from "@/lib/auth/cookie";
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

/** 写入 token 后：可选直达新会话，否则跳转 nextPath */
export async function finishLogin(
  router: AppRouter,
  accessToken: string,
  nextPath: string,
  afterLogin: string | null,
): Promise<void> {
  setAccessTokenCookie(accessToken);
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
