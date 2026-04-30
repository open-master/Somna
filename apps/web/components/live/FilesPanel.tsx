"use client";
import { FileText, FolderTree } from "lucide-react";

import { ScrollArea } from "@/components/ui/scroll-area";
import { useLiveStore } from "@/lib/store/live";
import { artifactPreviewUrl } from "@/lib/utils/artifact-links";

export function FilesPanel() {
  const artifacts = useLiveStore((s) => s.artifacts);
  const fileItems = useLiveStore((s) => s.fileItems);

  return (
    <ScrollArea className="h-full">
      <div className="p-3 space-y-2">
        {artifacts.length === 0 && fileItems.length === 0 ? (
          <p className="text-xs text-muted-foreground">暂无产出或文件轨迹。</p>
        ) : (
          <>
            {artifacts.map((f) => (
              <a
                key={f.id}
                href={artifactPreviewUrl(f.url)}
                target="_blank"
                rel="noreferrer"
                className="flex items-center gap-2 px-2 py-1.5 rounded-md hover:bg-accent text-sm"
              >
                <FileText className="size-4 text-muted-foreground" />
                <span className="flex-1 truncate">{f.name}</span>
                <span className="text-xs text-muted-foreground">{f.mime}</span>
              </a>
            ))}
            {fileItems.length > 0 ? (
              <div className="pt-2">
                <div className="mb-2 flex items-center gap-2 text-[11px] uppercase tracking-wide text-muted-foreground">
                  <FolderTree className="size-3.5" />
                  文件轨迹
                </div>
                <div className="space-y-1">
                  {fileItems.map((item) => (
                    <div
                      key={`${item.source}-${item.path}`}
                      className="rounded-md border px-2 py-1.5 text-xs"
                    >
                      <div className="truncate font-mono">{item.path}</div>
                      <div className="text-[11px] text-muted-foreground">{item.source}</div>
                    </div>
                  ))}
                </div>
              </div>
            ) : null}
          </>
        )}
      </div>
    </ScrollArea>
  );
}
