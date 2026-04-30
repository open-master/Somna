"use client";
import { useMemo } from "react";
import { Sparkles, Workflow } from "lucide-react";

import { Card, CardContent } from "@/components/ui/card";
import { ScrollArea } from "@/components/ui/scroll-area";
import { useChatStore } from "@/lib/store/chat";
import type { ChatMessage } from "@/lib/store/chat";

import { Composer } from "./Composer";
import { DeliverablesHub } from "./DeliverablesHub";
import { MessageList } from "./MessageList";
import { PlannerTimeline } from "./PlannerTimeline";
import { TaskFrameBanner } from "./TaskFrameBanner";
import { UserMessage } from "./UserMessage";

export function ChatCenter({ sessionId }: { sessionId: string }) {
  const messages = useChatStore((s) => s.messages);

  const lastUser = useMemo((): Extract<ChatMessage, { kind: "user" }> | null => {
    for (let i = messages.length - 1; i >= 0; i--) {
      const m = messages[i];
      if (m?.kind === "user") return m;
    }
    return null;
  }, [messages]);

  return (
    <div className="flex flex-1 min-h-0 flex-col bg-[linear-gradient(180deg,transparent,hsl(var(--muted)/0.28))]">
      {/* 固定在中间栏顶部：与 Manus 类似，任务计划 + 交付物总览不随下方聊天滚动消失 */}
      <div className="relative z-20 shrink-0 border-b border-border/70 bg-background/95 backdrop-blur-md supports-[backdrop-filter]:bg-background/80">
        <div className="mx-auto w-full max-w-3xl space-y-2 px-4 py-2">
          {lastUser ? (
            <div className="rounded-lg border border-border/80 bg-muted/20 px-3 py-2">
              <p className="mb-1.5 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
                用户问题
              </p>
              <UserMessage text={lastUser.text} />
            </div>
          ) : null}
          <TaskFrameBanner />
          <PlannerTimeline />
          <DeliverablesHub sessionId={sessionId} />
        </div>
      </div>

      <ScrollArea className="flex-1 min-h-0">
        <div className="mx-auto w-full max-w-4xl px-4 pt-4">
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
                      上方固定栏显示你的最新问题与任务进度；交付物汇总随执行更新；右侧可观察沙盒与轨迹。对话与执行日志在下方滚动显示。
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
        </div>
        <MessageList sessionId={sessionId} omitMessageId={lastUser?.id ?? null} />
      </ScrollArea>
      <Composer sessionId={sessionId} />
    </div>
  );
}
