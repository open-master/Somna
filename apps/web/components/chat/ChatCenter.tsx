"use client";
import { useEffect, useState } from "react";

import { ScrollArea } from "@/components/ui/scroll-area";
import { LiveComputerDock } from "@/components/live/LiveComputerDock";
import { getShowSkillDebugInChat, subscribeShowSkillDebugInChat } from "@/lib/skill-settings";
import { useChatStore } from "@/lib/store/chat";
import { useUiStore } from "@/lib/store/ui";

import { Composer } from "./Composer";
import { CreateSkillFromSessionCard } from "./CreateSkillFromSessionCard";
import { DeliverablesHub } from "./DeliverablesHub";
import { MessageList } from "./MessageList";
import { PlannerTimeline } from "./PlannerTimeline";
import { SkillDebugPanel } from "./SkillDebugPanel";
import { TaskFrameBanner } from "./TaskFrameBanner";

export function ChatCenter({ sessionId }: { sessionId: string }) {
  const messages = useChatStore((s) => s.messages);
  const hasConversation = messages.length > 0;
  const liveExpanded = useUiStore((s) => s.liveComputerExpanded);
  const [showSkillDebug, setShowSkillDebug] = useState(true);

  useEffect(() => {
    setShowSkillDebug(getShowSkillDebugInChat());
    return subscribeShowSkillDebugInChat(() => setShowSkillDebug(getShowSkillDebugInChat()));
  }, []);

  return (
    <div className="flex flex-1 min-h-0 flex-col bg-[linear-gradient(180deg,transparent,hsl(var(--muted)/0.28))]">
      {/* 有对话后再显示定调/计划/交付物，新会话保持顶栏简洁 */}
      {hasConversation ? (
        <div className="relative z-20 shrink-0 border-b border-border/70 bg-background/95 backdrop-blur-md supports-[backdrop-filter]:bg-background/80">
          <div className="mx-auto w-full max-w-3xl space-y-2 px-4 py-2">
            <TaskFrameBanner />
            <PlannerTimeline />
            {showSkillDebug ? <SkillDebugPanel /> : null}
            <DeliverablesHub sessionId={sessionId} />
          </div>
        </div>
      ) : null}

      <ScrollArea className="flex-1 min-h-0">
        <MessageList sessionId={sessionId} />
      </ScrollArea>
      {!liveExpanded ? (
        <div className="shrink-0 border-t border-border/60 bg-gradient-to-t from-background to-background/80 px-4 py-3">
          <div className="mx-auto w-full max-w-3xl">
            <LiveComputerDock />
          </div>
        </div>
      ) : null}
      {hasConversation ? <CreateSkillFromSessionCard sessionId={sessionId} /> : null}
      <Composer sessionId={sessionId} />
    </div>
  );
}
