"use client";
import { useEffect, useMemo, useRef } from "react";

import { ArtifactCard } from "./ArtifactCard";
import { AssistantMessage } from "./AssistantMessage";
import { ToolCallCard } from "./ToolCallCard";
import { UserMessage } from "./UserMessage";
import { useChatStore } from "@/lib/store/chat";

export function MessageList({
  sessionId,
  omitMessageId = null,
}: {
  sessionId: string;
  omitMessageId?: string | null;
}) {
  const messages = useChatStore((s) => s.messages);
  const bottomRef = useRef<HTMLDivElement>(null);

  const visible = useMemo(() => {
    if (omitMessageId === null) return messages;
    return messages.filter((m) => m.id !== omitMessageId);
  }, [messages, omitMessageId]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [visible.length]);

  return (
    <div className="mx-auto w-full max-w-3xl px-4 space-y-4 py-4">
      {visible.map((m) => {
        switch (m.kind) {
          case "user":
            return <UserMessage key={m.id} text={m.text} />;
          case "assistant":
            return <AssistantMessage key={m.id} text={m.text} thinking={m.thinking} sessionId={sessionId} />;
          case "tool":
            return (
              <ToolCallCard
                key={m.id}
                name={m.name}
                args={m.args}
                status={m.status}
                preview={m.preview}
                durationMs={m.durationMs}
              />
            );
          case "artifact":
            return <ArtifactCard key={m.id} sessionId={sessionId} name={m.name} mime={m.mime} url={m.url} />;
          default:
            return null;
        }
      })}
      <div ref={bottomRef} />
    </div>
  );
}
