"use client";
import {
  Loader2,
  CheckCircle2,
  XCircle,
  Terminal,
  Globe,
  FileText,
  Code2,
  Search,
  ChevronDown,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { ScrollArea } from "@/components/ui/scroll-area";
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
      <Collapsible className="min-w-0 flex-1" defaultOpen={false}>
        <Card className="overflow-hidden border-border/80 shadow-sm">
          <CollapsibleTrigger asChild>
            <button
              type="button"
              className={cn(
                "group flex w-full items-center gap-2 px-3 py-2.5 text-left text-xs transition-colors",
                "hover:bg-muted/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
                "border-b border-transparent data-[state=open]:border-border/50",
              )}
            >
              <ChevronDown
                className="size-4 shrink-0 text-muted-foreground transition-transform duration-200 group-data-[state=open]:rotate-180"
                aria-hidden
              />
              <span className="font-mono font-medium text-foreground">{name}</span>
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
              <span className="ml-auto truncate text-[11px] text-muted-foreground">
                {preview ? "含输出 · 点击展开" : "点击展开参数"}
              </span>
            </button>
          </CollapsibleTrigger>
          <CollapsibleContent>
            <CardContent className="space-y-3 border-t border-border/60 bg-muted/20 px-3 pb-3 pt-3">
              <div>
                <div className="mb-1.5 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                  参数
                </div>
                <ScrollArea className="h-36 w-full rounded-md border border-border/80 bg-background">
                  <pre
                    className={cn(
                      "p-2.5 text-xs font-mono leading-relaxed whitespace-pre-wrap break-all",
                      status === "failed" && "text-destructive",
                    )}
                  >
                    {argsStr}
                  </pre>
                </ScrollArea>
              </div>
              {preview ? (
                <div>
                  <div className="mb-1.5 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                    输出
                  </div>
                  <ScrollArea className="h-52 w-full rounded-md border border-border/80 bg-background">
                    <pre className="p-2.5 text-xs font-mono leading-relaxed whitespace-pre-wrap break-all">
                      {preview}
                    </pre>
                  </ScrollArea>
                </div>
              ) : null}
            </CardContent>
          </CollapsibleContent>
        </Card>
      </Collapsible>
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
