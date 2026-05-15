"use client";

import { Bug, ChevronDown } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { useSkillDebugStore } from "@/lib/store/skillDebug";
import { cn } from "@/lib/utils/cn";

export function SkillDebugPanel() {
  const candidateCount = useSkillDebugStore((s) => s.candidateCount);
  const selectedSkills = useSkillDebugStore((s) => s.selectedSkills);

  if (candidateCount === null) return null;

  const forcedCount = selectedSkills.filter((skill) => skill.forced).length;
  const title =
    selectedSkills.length > 0
      ? `Skill 调试：候选 ${candidateCount} · 选中 ${selectedSkills.length}`
      : `Skill 调试：候选 ${candidateCount} · 未选中`;

  return (
    <details className="group w-full max-w-3xl rounded-lg border border-dashed border-amber-300/70 bg-amber-50/60 px-3 py-2 text-sm dark:border-amber-500/40 dark:bg-amber-950/20">
      <summary
        className={cn(
          "flex cursor-pointer list-none items-center gap-2 select-none",
          "[&::-webkit-details-marker]:hidden",
        )}
      >
        <Bug className="size-4 shrink-0 text-amber-700 dark:text-amber-300" />
        <span className="min-w-0 flex-1 font-medium text-foreground">{title}</span>
        {forcedCount > 0 ? (
          <Badge variant="secondary" className="shrink-0">
            强制注入 {forcedCount}
          </Badge>
        ) : null}
        <ChevronDown className="size-4 shrink-0 text-muted-foreground transition group-open:rotate-180" />
      </summary>

      <div className="mt-2 space-y-2 text-xs text-muted-foreground">
        <div className="grid gap-1 sm:grid-cols-3">
          <DebugMetric label="候选 Skill" value={String(candidateCount)} />
          <DebugMetric label="Router 选中" value={String(selectedSkills.length)} />
          <DebugMetric label="Task Frame 强制注入" value={forcedCount > 0 ? "是" : "否"} />
        </div>

        {selectedSkills.length > 0 ? (
          <div className="space-y-2">
            {selectedSkills.map((skill) => (
              <div key={skill.id} className="rounded-md bg-background/70 px-2 py-1.5">
                <div className="flex flex-wrap items-center gap-1.5">
                  <span className="font-mono font-medium text-foreground">{skill.name}</span>
                  {skill.forced ? <Badge variant="secondary">Task Frame 强制注入</Badge> : null}
                </div>
                {skill.reason ? <p className="mt-1 whitespace-pre-wrap">{skill.reason}</p> : null}
                <div className="mt-1">
                  <span className="font-medium text-foreground">注入文件：</span>
                  {skill.load_files.length > 0 ? (
                    <span className="font-mono">{skill.load_files.join(", ")}</span>
                  ) : (
                    <span>无</span>
                  )}
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div className="rounded-md bg-background/70 px-2 py-1.5">
            Router 未选择任何 Skill；若 Task Frame 已点名某个 Skill，请检查该 Skill 是否在当前用户候选集中且处于启用状态。
          </div>
        )}
      </div>
    </details>
  );
}

function DebugMetric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md bg-background/70 px-2 py-1.5">
      <div className="text-[11px] text-muted-foreground">{label}</div>
      <div className="font-medium text-foreground">{value}</div>
    </div>
  );
}
