"use client";
import { Download, ExternalLink, FileText, FolderTree } from "lucide-react";

import { PptxAwareLink } from "@/components/chat/PptxAwarePreview";
import { ScrollArea } from "@/components/ui/scroll-area";
import { useLiveStore } from "@/lib/store/live";
import { artifactDownloadUrl, artifactPreviewUrl, parseArtifactPathFromUrl } from "@/lib/utils/artifact-links";
import { sessionArtifactContentUrl } from "@/lib/utils/deliverable-resolve";
import { isSessionArtifactRelativePath } from "@/lib/utils/workspace-path";

function artifactItemFetchUrl(
  sessionId: string,
  f: { name: string; description?: string; url: string },
): string {
  const d = (f.description ?? "").trim().replace(/^\.\//, "");
  if (d) return sessionArtifactContentUrl(sessionId, d);
  const fromUrl = parseArtifactPathFromUrl(f.url);
  if (fromUrl) return sessionArtifactContentUrl(sessionId, fromUrl);
  return sessionArtifactContentUrl(sessionId, f.name.replace(/^\.\//, ""));
}

export function FilesPanel({ sessionId }: { sessionId: string }) {
  const artifacts = useLiveStore((s) => s.artifacts);
  const fileItems = useLiveStore((s) => s.fileItems);

  return (
    <ScrollArea className="h-full">
      <div className="p-3 space-y-2">
        {artifacts.length === 0 && fileItems.length === 0 ? (
          <p className="text-xs text-muted-foreground">暂无产出或文件轨迹。</p>
        ) : (
          <>
            {artifacts.map((f) => {
              const fetchUrl = artifactItemFetchUrl(sessionId, f);
              return (
              <PptxAwareLink
                key={f.id}
                previewHref={artifactPreviewUrl(fetchUrl)}
                fileName={f.name}
                mime={f.mime}
                title="预览"
                className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm hover:bg-accent"
              >
                <FileText className="size-4 text-muted-foreground" />
                <span className="flex-1 truncate">{f.name}</span>
                <span className="text-xs text-muted-foreground">{f.mime}</span>
              </PptxAwareLink>
            );
            })}
            {fileItems.length > 0 ? (
              <div className="pt-2">
                <div className="mb-2 flex items-center gap-2 text-[11px] uppercase tracking-wide text-muted-foreground">
                  <FolderTree className="size-3.5" />
                  文件轨迹
                </div>
                <div className="space-y-1">
                  {fileItems.map((item) => {
                    const canOpen = Boolean(sessionId) && isSessionArtifactRelativePath(item.path);
                    const fetchUrl = canOpen ? sessionArtifactContentUrl(sessionId, item.path) : null;
                    return (
                      <div
                        key={`${item.source}-${item.path}`}
                        className="rounded-md border px-2 py-1.5 text-xs"
                      >
                        <div className="truncate font-mono">{item.path}</div>
                        <div className="mt-1 flex flex-wrap items-center gap-2">
                          <span className="text-[11px] text-muted-foreground">{item.source}</span>
                          {fetchUrl ? (
                            <>
                              <PptxAwareLink
                                previewHref={artifactPreviewUrl(fetchUrl)}
                                fileName={item.path.split("/").pop() ?? item.path}
                                mime=""
                                title="预览"
                                className="inline-flex cursor-pointer items-center gap-0.5 border-0 bg-transparent p-0 text-[11px] text-primary hover:underline"
                              >
                                <ExternalLink className="size-3" />
                                预览
                              </PptxAwareLink>
                              <a
                                href={artifactDownloadUrl(fetchUrl)}
                                className="inline-flex items-center gap-0.5 text-[11px] text-muted-foreground hover:text-foreground hover:underline"
                              >
                                <Download className="size-3" />
                                下载
                              </a>
                            </>
                          ) : null}
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            ) : null}
          </>
        )}
      </div>
    </ScrollArea>
  );
}
