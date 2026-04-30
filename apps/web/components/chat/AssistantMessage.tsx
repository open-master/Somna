"use client";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Sparkles } from "lucide-react";

export function AssistantMessage({ text, thinking }: { text: string; thinking?: string }) {
  return (
    <div className="flex gap-3 animate-fade-in">
      <div className="size-7 shrink-0 rounded-full bg-primary/10 text-primary grid place-items-center">
        <Sparkles className="size-4" />
      </div>
      <div className="flex-1 min-w-0 space-y-2">
        {thinking ? (
          <details className="text-xs text-muted-foreground">
            <summary className="cursor-pointer select-none hover:text-foreground transition-colors">
              展开思考…
            </summary>
            <pre className="mt-1 whitespace-pre-wrap font-sans text-xs leading-relaxed bg-muted/40 rounded-md p-2 max-h-56 overflow-auto scrollbar-thin">
              {thinking}
            </pre>
          </details>
        ) : null}
        {text ? (
          <article className="prose prose-sm max-w-none dark:prose-invert prose-p:my-1 prose-pre:my-2">
            <ReactMarkdown
              remarkPlugins={[remarkGfm]}
              components={{
                a: ({ href, children, ...props }) => {
                  const isApi = typeof href === "string" && href.startsWith("/api/");
                  return (
                    <a
                      href={href}
                      {...props}
                      {...(isApi ? { target: "_blank", rel: "noopener noreferrer" } : {})}
                    >
                      {children}
                    </a>
                  );
                },
              }}
            >
              {text}
            </ReactMarkdown>
          </article>
        ) : (
          <div className="text-xs text-muted-foreground italic">思考中...</div>
        )}
      </div>
    </div>
  );
}
