"use client";
import { Monitor } from "lucide-react";

import { useLiveStore } from "@/lib/store/live";

function screenshotSourceLabel(source: "browser" | "desktop" | "custom"): string {
  switch (source) {
    case "custom":
      return "Workspace 交付物预览";
    case "browser":
      return "浏览器画面";
    case "desktop":
      return "桌面画面";
    default:
      return source;
  }
}

export function ScreenPanel() {
  const latest = useLiveStore((s) => s.latestScreenshot);
  const screenshots = useLiveStore((s) => s.screenshots);

  return (
    <div className="h-full flex flex-col">
      <div className="flex-1 min-h-0 flex items-center justify-center bg-black/90 overflow-hidden">
        {latest ? (
          <img
            src={latest.url}
            alt="agent-screen"
            className="max-h-full max-w-full object-contain"
          />
        ) : (
          <div className="text-muted-foreground text-sm flex flex-col items-center gap-2">
            <Monitor className="size-8 opacity-40" />
            等待 Agent 打开屏幕...
          </div>
        )}
      </div>
      {latest ? (
        <>
          <div className="px-3 py-1.5 text-xs text-muted-foreground border-t bg-background/70 flex justify-between">
            <span>{screenshotSourceLabel(latest.source)}</span>
            <span className="font-mono">{new Date(latest.ts).toLocaleTimeString()}</span>
          </div>
          {screenshots.length > 1 ? (
            <div className="border-t bg-background/60 px-2 py-2">
              <div className="flex gap-2 overflow-x-auto">
                {screenshots.slice(-8).reverse().map((shot) => (
                  <a
                    key={`${shot.ts}-${shot.url}`}
                    href={shot.url}
                    target="_blank"
                    rel="noreferrer"
                    className="shrink-0 overflow-hidden rounded border bg-black"
                    title={new Date(shot.ts).toLocaleTimeString()}
                  >
                    <img
                      src={shot.url}
                      alt="agent-screen-history"
                      className="h-16 w-24 object-cover opacity-80 hover:opacity-100"
                    />
                  </a>
                ))}
              </div>
            </div>
          ) : null}
        </>
      ) : null}
    </div>
  );
}
