"use client";
import { Check, Circle, Loader2, XCircle, Minus } from "lucide-react";

import { usePlanStore } from "@/lib/store/plan";
import { cn } from "@/lib/utils/cn";

export function PlannerTimeline() {
  const todos = usePlanStore((s) => s.todos);
  if (todos.length === 0) return null;

  const done = todos.filter((t) => t.status === "done").length;

  return (
    <details className="mx-auto w-full max-w-3xl rounded-lg border bg-card px-3 py-2 group">
      <summary className="cursor-pointer flex items-center gap-2 text-sm font-medium list-none select-none">
        <span className="inline-flex size-5 items-center justify-center rounded-md bg-primary/10 text-primary text-xs">
          {done}
        </span>
        <span>
          任务计划（<span className="font-mono">{done}</span>/<span className="font-mono">{todos.length}</span> 已完成）
        </span>
        <span className="text-xs text-muted-foreground ml-auto">点击展开</span>
      </summary>
      <ol className="mt-2 space-y-1 text-sm">
        {todos.map((t) => (
          <li key={t.id} className="flex items-center gap-2">
            {iconForStatus(t.status)}
            <span
              className={cn(
                t.status === "done" && "text-muted-foreground line-through",
                t.status === "failed" && "text-destructive",
              )}
            >
              {t.text}
            </span>
          </li>
        ))}
      </ol>
    </details>
  );
}

function iconForStatus(status: string) {
  switch (status) {
    case "done":
      return <Check className="size-3.5 text-emerald-600" />;
    case "in_progress":
      return <Loader2 className="size-3.5 text-amber-600 animate-spin" />;
    case "failed":
      return <XCircle className="size-3.5 text-destructive" />;
    case "skipped":
      return <Minus className="size-3.5 text-muted-foreground" />;
    default:
      return <Circle className="size-3.5 text-muted-foreground" />;
  }
}
