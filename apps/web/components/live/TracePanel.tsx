"use client";
import { ScrollArea } from "@/components/ui/scroll-area";
import { useLiveStore } from "@/lib/store/live";

export function TracePanel() {
  const events = useLiveStore((s) => s.recentEvents);
  return (
    <ScrollArea className="h-full">
      <ol className="p-3 space-y-1 text-xs font-mono">
        {events.length === 0 ? (
          <li className="text-muted-foreground">暂无事件</li>
        ) : (
          events.map((e, i) => (
            <li key={i} className="flex gap-2">
              <span className="shrink-0 text-muted-foreground/70">
                {new Date(e.ts).toLocaleTimeString()}
              </span>
              <span className="text-muted-foreground shrink-0">
                #{e.seq ?? "-"}
              </span>
              <span className="shrink-0 text-primary">{e.type}</span>
              <span className="truncate text-muted-foreground">{e.summary}</span>
            </li>
          ))
        )}
      </ol>
    </ScrollArea>
  );
}
