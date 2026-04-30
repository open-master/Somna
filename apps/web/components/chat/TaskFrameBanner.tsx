"use client";

import { ChevronDown, Sparkles } from "lucide-react";

import { useTaskFrameStore } from "@/lib/store/taskFrame";
import { cn } from "@/lib/utils/cn";

/**
 * 阶段 A 任务定调结论：展示在「任务计划」上方，与 Manus 式「先理解再执行」对齐。
 */
export function TaskFrameBanner() {
  const summary = useTaskFrameStore((s) => s.summary);
  const detail = useTaskFrameStore((s) => s.detail);
  if (!summary) return null;

  return (
    <details className="group w-full max-w-3xl rounded-lg border border-primary/20 bg-primary/5 px-3 py-2 text-sm">
      <summary
        className={cn(
          "flex cursor-pointer list-none items-center gap-2 select-none",
          "[&::-webkit-details-marker]:hidden",
        )}
      >
        <Sparkles className="size-4 shrink-0 text-primary" />
        <span className="min-w-0 flex-1 font-medium text-foreground">{summary}</span>
        <ChevronDown className="size-4 shrink-0 text-muted-foreground transition group-open:rotate-180" />
      </summary>
      {detail && detail !== "(无)" ? (
        <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap break-words rounded-md bg-muted/50 px-2 py-1.5 text-xs text-muted-foreground">
          {detail}
        </pre>
      ) : null}
    </details>
  );
}
