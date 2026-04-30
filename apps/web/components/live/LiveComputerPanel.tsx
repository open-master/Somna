"use client";
import { Monitor, TerminalSquare, FolderOpen, Activity } from "lucide-react";

import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";

import { FilesPanel } from "./FilesPanel";
import { ScreenPanel } from "./ScreenPanel";
import { TerminalPanel } from "./TerminalPanel";
import { TracePanel } from "./TracePanel";

export function LiveComputerPanel() {
  return (
    <div className="flex flex-col h-full">
      <div className="flex h-16 shrink-0 items-center justify-between border-b px-4">
        <div className="flex items-center gap-2">
          <span className="inline-block size-2 rounded-full bg-emerald-500 animate-pulse" />
          <div>
            <div className="text-sm font-medium">Live Computer</div>
            <div className="text-xs text-muted-foreground">屏幕、文件、终端与事件正在同步</div>
          </div>
        </div>
        <span className="rounded-full border px-2.5 py-1 text-xs text-muted-foreground">Agent 观察区</span>
      </div>
      <Tabs defaultValue="screen" className="flex-1 flex flex-col min-h-0">
        <TabsList className="mx-3 mt-3 w-auto self-start rounded-xl bg-muted/60 p-1">
          <TabsTrigger value="screen" className="gap-1.5">
            <Monitor className="size-3.5" /> 屏幕
          </TabsTrigger>
          <TabsTrigger value="terminal" className="gap-1.5">
            <TerminalSquare className="size-3.5" /> 终端
          </TabsTrigger>
          <TabsTrigger value="files" className="gap-1.5">
            <FolderOpen className="size-3.5" /> 文件
          </TabsTrigger>
          <TabsTrigger value="trace" className="gap-1.5">
            <Activity className="size-3.5" /> 轨迹
          </TabsTrigger>
        </TabsList>
        <TabsContent value="screen" className="flex-1 min-h-0 mt-2">
          <ScreenPanel />
        </TabsContent>
        <TabsContent value="terminal" className="flex-1 min-h-0 mt-2">
          <TerminalPanel />
        </TabsContent>
        <TabsContent value="files" className="flex-1 min-h-0 mt-2">
          <FilesPanel />
        </TabsContent>
        <TabsContent value="trace" className="flex-1 min-h-0 mt-2">
          <TracePanel />
        </TabsContent>
      </Tabs>
    </div>
  );
}
