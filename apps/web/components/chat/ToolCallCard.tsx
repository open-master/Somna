"use client";
import { Loader2, CheckCircle2, XCircle, Terminal, Globe, FileText, Code2, Search } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils/cn";

const TOOL_ICON: Record<string, React.ComponentType<{ className?: string }>> = {
  shell: Terminal,
  browser: Globe,
  filesystem: FileText,
  file: FileText,
  code_exec: Code2,
  search: Search,
};

export function ToolCallCard({
  name,
  args,
  status,
  preview,
  durationMs,
}: {
  name: string;
  args: unknown;
  status: "running" | "ok" | "failed";
  preview?: string;
  durationMs?: number;
}) {
  const Icon = TOOL_ICON[name] ?? Terminal;
  const argsStr = safeStringify(args);

  return (
    <div className="flex gap-3 animate-fade-in">
      <div className="size-7 shrink-0 rounded-full bg-muted grid place-items-center">
        <Icon className="size-3.5" />
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 text-xs">
          <span className="font-mono font-medium">{name}</span>
          {status === "running" ? (
            <Badge variant="warning" className="gap-1">
              <Loader2 className="size-3 animate-spin" /> running
            </Badge>
          ) : status === "ok" ? (
            <Badge variant="success" className="gap-1">
              <CheckCircle2 className="size-3" /> ok
            </Badge>
          ) : (
            <Badge variant="destructive" className="gap-1">
              <XCircle className="size-3" /> failed
            </Badge>
          )}
          {typeof durationMs === "number" ? (
            <span className="text-muted-foreground">· {durationMs} ms</span>
          ) : null}
        </div>
        <pre
          className={cn(
            "mt-1 text-xs font-mono bg-muted/50 rounded-md p-2 whitespace-pre-wrap break-all",
            status === "failed" && "border border-destructive/30",
          )}
        >
          {argsStr}
        </pre>
        {preview ? (
          <pre className="mt-1 text-xs font-mono bg-background border rounded-md p-2 whitespace-pre-wrap break-all max-h-40 overflow-auto scrollbar-thin">
            {preview}
          </pre>
        ) : null}
      </div>
    </div>
  );
}

function safeStringify(v: unknown): string {
  try {
    return typeof v === "string" ? v : JSON.stringify(v, null, 2);
  } catch {
    return String(v);
  }
}
