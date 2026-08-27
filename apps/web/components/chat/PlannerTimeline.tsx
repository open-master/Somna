"use client";
import { Check, ChevronDown, Circle, Loader2, Minus, XCircle } from "lucide-react";

import { usePlanStore } from "@/lib/store/plan";
import { cn } from "@/lib/utils/cn";

export function PlannerTimeline() {
  const todos = usePlanStore((s) => s.todos);
  const planVersion = usePlanStore((s) => s.planVersion);
  if (todos.length === 0) return null;

  const done = todos.filter((t) => t.status === "done").length;
  const failed = todos.filter((t) => t.status === "failed").length;
  const skipped = todos.filter((t) => t.status === "skipped").length;

  return (
    <details className="mx-auto w-full max-w-3xl rounded-lg border bg-card px-3 py-2 group">
      <summary
        className={cn(
          "flex cursor-pointer list-none flex-wrap items-center gap-2 text-sm font-medium select-none",
          "[&::-webkit-details-marker]:hidden",
        )}
      >
        <span className="inline-flex size-5 shrink-0 items-center justify-center rounded-md bg-primary/10 text-primary text-xs">
          {done}
        </span>
        <span>
          任务计划（<span className="font-mono">{done}</span>/<span className="font-mono">{todos.length}</span>{" "}
          已完成
          {failed > 0 ? <> · <span className="font-mono text-destructive">{failed}</span> 失败</> : null}
          {skipped > 0 ? <> · <span className="font-mono text-muted-foreground">{skipped}</span> 跳过</> : null}
          ）
        </span>
        {planVersion > 1 ? (
          <span className="rounded bg-muted px-1.5 py-0.5 text-[11px] text-muted-foreground">
            v{planVersion}
          </span>
        ) : null}
        <ChevronDown className="ml-auto size-4 shrink-0 text-muted-foreground transition-transform group-open:rotate-180" />
      </summary>
      <ol className="mt-2 space-y-1 text-sm">
        {todos.map((t) => (
          <li key={t.id} className="flex items-start gap-2 py-0.5">
            <span className="mt-0.5">{iconForStatus(t.status)}</span>
            <div className="min-w-0 flex-1">
              <div
                className={cn(
                  t.status === "done" && "text-muted-foreground line-through",
                  t.status === "failed" && "text-destructive",
                )}
              >
                {t.text}
              </div>
              {t.status === "done" && t.completion_reason ? (
                <div className="mt-0.5 text-xs text-emerald-700/80 no-underline">
                  完成依据：{t.completion_reason}
                </div>
              ) : null}
              {t.status === "failed" && t.failure_reason ? (
                <div className="mt-0.5 text-xs text-destructive/80">
                  失败原因：{t.failure_reason}
                </div>
              ) : null}
              {t.status === "skipped" && t.failure_reason ? (
                <div className="mt-0.5 text-xs text-muted-foreground">
                  未执行原因：{t.failure_reason}
                </div>
              ) : null}
              {t.status === "in_progress" && t.acceptance_criteria.length > 0 ? (
                <div className="mt-0.5 text-xs text-muted-foreground">
                  验收：{t.acceptance_criteria.join("；")}
                </div>
              ) : null}
              {t.evidence_paths.length > 0 ? (
                <div className="mt-0.5 truncate font-mono text-[11px] text-muted-foreground" title={t.evidence_paths.join("\n")}>
                  证据：{t.evidence_paths.slice(0, 3).join(" · ")}
                </div>
              ) : null}
            </div>
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
