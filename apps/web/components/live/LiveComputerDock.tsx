"use client";

import { ChevronUp, Monitor } from "lucide-react";

import { Card } from "@/components/ui/card";
import { useLiveStore } from "@/lib/store/live";
import { useSessionStore } from "@/lib/store/session";
import { useUiStore } from "@/lib/store/ui";
import { cn } from "@/lib/utils/cn";

/**
 * 收起右侧 Agent 观察区时，在中间区域输入框上方显示的「小窗」入口（参考 Manus）。
 * 点击后在右侧展开完整面板（由 AppShell 根据 ui store 渲染）。
 */
export function LiveComputerDock() {
  const openLiveComputer = useUiStore((s) => s.openLiveComputer);
  const latest = useLiveStore((s) => s.latestScreenshot);
  const recent = useLiveStore((s) => s.recentEvents[0]);
  const phase = useSessionStore((s) => s.phase);

  const line =
    recent?.summary?.trim() ||
    (phase === "idle"
      ? "点击查看 Agent 观察区（屏幕、终端、文件与轨迹）"
      : `Agent 状态：${phase ?? "—"} · 点击在右侧展开`);

  return (
    <button
      type="button"
      onClick={() => openLiveComputer()}
      className="group w-full text-left outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 rounded-2xl"
      aria-label="展开 Agent 观察区"
    >
      <Card
        className={cn(
          "flex cursor-pointer flex-row items-stretch gap-3 overflow-hidden border-border/80 bg-card/95 p-3 shadow-md",
          "transition-shadow hover:shadow-lg hover:border-primary/25",
        )}
      >
        <div className="relative h-14 w-24 shrink-0 overflow-hidden rounded-lg border bg-muted/60">
          {latest ? (
            <img src={latest.url} alt="" className="size-full object-cover" />
          ) : (
            <div className="flex size-full items-center justify-center text-muted-foreground">
              <Monitor className="size-6 opacity-50" />
            </div>
          )}
        </div>
        <div className="flex min-w-0 flex-1 flex-col justify-center gap-0.5">
          <p className="text-xs font-medium text-muted-foreground">Agent 观察区</p>
          <p className="line-clamp-2 text-sm leading-snug text-foreground">{line}</p>
        </div>
        <div className="flex shrink-0 flex-col items-center justify-center gap-1 text-muted-foreground">
          <ChevronUp className="size-4 opacity-70 transition group-hover:opacity-100" />
        </div>
      </Card>
    </button>
  );
}
