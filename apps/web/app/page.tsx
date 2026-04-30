"use client";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { Sparkles, ArrowRight } from "lucide-react";

import { Button } from "@/components/ui/button";
import { createSession } from "@/lib/api/sessions";
import { useSessionStore } from "@/lib/store/session";

export default function HomePage() {
  const router = useRouter();
  const upsert = useSessionStore((s) => s.upsertSession);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function start() {
    setLoading(true);
    setError(null);
    try {
      const s = await createSession("新会话");
      upsert({ id: s.id, title: s.title, updatedAt: new Date().toISOString() });
      router.push(`/chat/${s.id}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    // pre-focus check could go here
  }, []);

  return (
    <main className="h-screen w-screen grid place-items-center bg-gradient-to-b from-background to-muted/30">
      <div className="max-w-xl text-center space-y-6 px-6">
        <div className="inline-flex items-center gap-2 rounded-full border px-3 py-1 text-xs text-muted-foreground">
          <Sparkles className="size-3.5 text-primary" /> Somna AI · Manus-like autonomous agent
        </div>
        <h1 className="text-4xl font-semibold tracking-tight">
          把任务交给一个<span className="text-primary"> 会打开浏览器 </span>的 Agent
        </h1>
        <p className="text-muted-foreground">
          它有屏幕、文件、终端和记忆。你给一个目标，它给你结果。
        </p>
        <div className="flex items-center justify-center gap-3">
          <Button size="lg" onClick={start} disabled={loading} className="gap-1">
            {loading ? "创建中..." : "开始新会话"} <ArrowRight className="size-4" />
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
    </main>
  );
}
