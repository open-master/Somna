"use client";
import { ScrollArea } from "@/components/ui/scroll-area";
import { useLiveStore } from "@/lib/store/live";

export function TerminalPanel() {
  const lines = useLiveStore((s) => s.terminalLines);
  return (
    <ScrollArea className="h-full bg-black text-green-400 font-mono text-xs">
      <pre className="p-3 whitespace-pre-wrap leading-relaxed">
        {lines.length === 0 ? "# 等待 shell 输出..." : lines.join("\n")}
      </pre>
    </ScrollArea>
  );
}
