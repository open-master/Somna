"use client";

import { Check, Sparkles } from "lucide-react";
import { useMemo, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { createSkillFromSession, listSkills, type SkillRow } from "@/lib/api/skills";
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
    let snapshotOk = false;
    const idsBefore = new Set<string>();
    try {
      const existing = await listSkills("my");
      for (const r of existing) idsBefore.add(r.id);
      snapshotOk = true;
    } catch {
      /* 无快照时不做「响应丢失」恢复，避免误判别人的 Skill */
    }
    try {
      const me = await meRequest();
      const visibility = me?.role === "admin" ? "shared" : "private";
      setCreated(await createSkillFromSession(sessionId, visibility));
    } catch (e) {
      const msg = e instanceof Error ? e.message : "创建 Skill 失败";
      /* 后端常已写库成功，但代理/网关超时导致浏览器收到 5xx；用技能 id 快照对齐 */
      if (snapshotOk) {
        try {
          const after = await listSkills("my");
          const newcomers = after.filter((r) => !idsBefore.has(r.id));
          if (newcomers.length > 0) {
            const candidate = [...newcomers].sort((a, b) =>
              (b.updated_at ?? "").localeCompare(a.updated_at ?? ""),
            )[0];
            if (candidate) {
              setCreated(candidate);
              setError(null);
              return;
            }
          }
        } catch {
          /* ignore */
        }
      }
      setError(msg);
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
