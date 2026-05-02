"use client";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { Activity, ArrowRight, Sparkles } from "lucide-react";

import { Button } from "@/components/ui/button";
import { createSession } from "@/lib/api/sessions";
import { meRequest } from "@/lib/api/auth";
import { buildAuthHref } from "@/lib/auth/finish-login";
import { useSessionStore } from "@/lib/store/session";

export default function HomePage() {
  const router = useRouter();
  const upsert = useSessionStore((s) => s.upsertSession);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [authed, setAuthed] = useState<boolean | null>(null);

  const loginHref = buildAuthHref("/login", { afterLogin: "new-session" });
  const registerHref = buildAuthHref("/register", { afterLogin: "new-session" });

  useEffect(() => {
    void meRequest().then((u) => setAuthed(!!u));
  }, []);

  async function start() {
    let ok = authed;
    if (ok === null) {
      ok = (await meRequest()) != null;
      setAuthed(ok);
    }
    if (!ok) {
      router.push(loginHref);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const s = await createSession();
      upsert({
        id: s.id,
        title: s.title,
        updatedAt: s.updated_at ?? new Date().toISOString(),
        status: s.status,
        runId: s.run_id ?? null,
        workflowId: s.workflow_id ?? null,
        createdAt: s.created_at,
      });
      router.push(`/chat/${s.id}`);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      if (msg === "401" || msg.includes("401")) {
        router.push(loginHref);
        return;
      }
      setError(msg);
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="relative h-screen w-screen bg-gradient-to-b from-background to-muted/30">
      <header className="absolute inset-x-0 top-0 z-10 flex items-center justify-between px-6 py-4">
        <Link href="/" className="flex items-center gap-2.5 text-sm font-semibold tracking-tight">
          <span className="grid size-9 shrink-0 place-items-center rounded-xl border bg-background shadow-sm">
            <Activity className="size-4 text-primary" />
          </span>
          Somna
        </Link>
        <div className="flex items-center gap-2">
          <Button variant="ghost" size="sm" asChild>
            <Link href={loginHref}>登录</Link>
          </Button>
          <Button size="sm" asChild>
            <Link href={registerHref}>注册</Link>
          </Button>
        </div>
      </header>

      <div className="grid h-full place-items-center">
        <div className="max-w-xl space-y-6 px-6 text-center">
          <div className="inline-flex items-center gap-2 rounded-full border px-3 py-1 text-xs text-muted-foreground">
            <Sparkles className="size-3.5 text-primary" /> Somna · Manus-like autonomous agent
          </div>
          <h1 className="text-4xl font-semibold tracking-tight">
            把任务交给一个<span className="text-primary"> 会打开浏览器 </span>的 Agent
          </h1>
          <p className="text-muted-foreground">
            它有屏幕、文件、终端和记忆。你给一个目标，它给你结果。
          </p>
          <div className="flex flex-wrap items-center justify-center gap-3">
            <Button size="lg" onClick={() => void start()} disabled={loading} className="gap-1">
              {loading ? "创建中…" : "开始新会话"} <ArrowRight className="size-4" />
            </Button>
          </div>
          {error ? (
            <p className="text-xs text-destructive">{error}</p>
          ) : (
            <p className="text-xs text-muted-foreground">
              后端未就绪？请先 <code className="font-mono">make up</code>，再刷新。
            </p>
          )}
        </div>
      </div>
    </main>
  );
}
