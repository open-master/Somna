"use client";
import { Sparkles, Workflow } from "lucide-react";

import { Card, CardContent } from "@/components/ui/card";
import { ScrollArea } from "@/components/ui/scroll-area";
import { useChatStore } from "@/lib/store/chat";

import { Composer } from "./Composer";
import { MessageList } from "./MessageList";
import { PlannerTimeline } from "./PlannerTimeline";

export function ChatCenter({ sessionId }: { sessionId: string }) {
  const messages = useChatStore((s) => s.messages);

  return (
    <div className="flex flex-1 min-h-0 flex-col bg-[linear-gradient(180deg,transparent,hsl(var(--muted)/0.28))]">
      <ScrollArea className="flex-1 min-h-0">
        <div className="mx-auto w-full max-w-4xl px-4 pt-5">
          {messages.length === 0 ? (
            <Card className="mb-4 overflow-hidden rounded-[28px] border-primary/10 bg-background/85 shadow-sm">
              <CardContent className="p-0">
                <div className="border-b bg-muted/40 px-6 py-4">
                  <div className="inline-flex items-center gap-2 rounded-full border bg-background px-3 py-1 text-xs text-muted-foreground">
                    <Sparkles className="size-3.5 text-primary" />
                    Somna Workspace
                  </div>
                </div>
                <div className="grid gap-6 px-6 py-6 lg:grid-cols-[minmax(0,1.6fr)_minmax(280px,1fr)]">
                  <div className="space-y-3">
                    <h2 className="text-2xl font-semibold tracking-tight">
                      给出一个目标，Somna 会自己规划、执行、反思并交付结果。
                    </h2>
                    <p className="max-w-2xl text-sm leading-6 text-muted-foreground">
                      现在这页已经不只是聊天窗口，而是一个 Agent 工作区。左边管理会话与任务，右边实时观察屏幕、文件与轨迹，中间负责交互与交付。
                    </p>
                  </div>
                  <div className="rounded-3xl border bg-muted/35 p-4 text-sm">
                    <div className="flex items-center gap-2 font-medium">
                      <Workflow className="size-4 text-primary" />
                      当前工作方式
                    </div>
                    <ul className="mt-3 space-y-2 text-muted-foreground">
                      <li>1. 输入目标或任务。</li>
                      <li>2. Agent 自动生成计划并开始执行。</li>
                      <li>3. 在右侧观察执行过程，并在需要时打断或停止。</li>
                    </ul>
                  </div>
                </div>
              </CardContent>
            </Card>
          ) : null}
          <PlannerTimeline />
        </div>
        <MessageList />
      </ScrollArea>
      <Composer sessionId={sessionId} />
    </div>
  );
}
