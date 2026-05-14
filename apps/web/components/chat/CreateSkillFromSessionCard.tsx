"use client";

import { Check, Sparkles } from "lucide-react";
import { useMemo, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { createSkillFromSession, type SkillRow } from "@/lib/api/skills";
import { meRequest } from "@/lib/api/auth";
import { useSessionStore } from "@/lib/store/session";

export function CreateSkillFromSessionCard({ sessionId }: { sessionId: string }) {
  const current = useSessionStore((s) => s.sessions.find((item) => item.id === sessionId));
  const phase = useSessionStore((s) => s.phase);
  const [creating, setCreating] = useState(false);
  const [created, setCreated] = useState<SkillRow | null>(null);
  const [error, setError] = useState<string | null>(null);

  const shouldShow = useMemo(() => {
    if (created) return true;
    return phase === "done" || current?.lastRunTerminal === "success";
  }, [created, current?.lastRunTerminal, phase]);

  if (!shouldShow) return null;

  async function create() {
    setCreating(true);
    setError(null);
    try {
      const me = await meRequest();
      const visibility = me?.role === "admin" ? "shared" : "private";
      setCreated(await createSkillFromSession(sessionId, visibility));
    } catch (e) {
      setError(e instanceof Error ? e.message : "创建 Skill 失败");
    } finally {
      setCreating(false);
    }
  }

  return (
    <div className="mx-auto w-full max-w-3xl px-4 pb-2">
      <div className="rounded-2xl border bg-card/90 p-3 shadow-sm">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              {created ? (
                <Check className="size-4 text-emerald-600" />
              ) : (
                <Sparkles className="size-4 text-primary" />
              )}
              <p className="text-sm font-medium">
                {created ? "已保存为 Skill" : "把这次任务沉淀为 Skill"}
              </p>
              {created ? (
                <Badge variant={created.visibility === "official" ? "success" : "outline"}>
                  {created.visibility === "official" ? "官方" : "已启用"}
                </Badge>
              ) : null}
            </div>
            <p className="mt-1 text-xs text-muted-foreground">
              {created
                ? `后续相似任务会优先参考 ${created.name}。`
                : "生成一个 Claude 标准 Skill，后续遇到相似任务时自动复用这套工作方法。"}
            </p>
            {error ? <p className="mt-1 text-xs text-destructive">{error}</p> : null}
          </div>
          {!created ? (
            <Button type="button" size="sm" onClick={() => void create()} disabled={creating}>
              {creating ? "生成中…" : "创建 Skill"}
            </Button>
          ) : null}
        </div>
      </div>
    </div>
  );
}
