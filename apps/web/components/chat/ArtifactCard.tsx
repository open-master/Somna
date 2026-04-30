import { Download, FileText, ExternalLink } from "lucide-react";

import { PptxAwareLink, PptxAwarePreviewButton } from "@/components/chat/PptxAwarePreview";
import { artifactDownloadUrl, artifactPreviewUrl, parseArtifactPathFromUrl } from "@/lib/utils/artifact-links";
import { sessionArtifactContentUrl } from "@/lib/utils/deliverable-resolve";

export function ArtifactCard({
  sessionId,
  name,
  mime,
  url,
}: {
  sessionId: string;
  name: string;
  mime: string;
  url: string;
}) {
  const fromQuery = parseArtifactPathFromUrl(url);
  const rel = (fromQuery && fromQuery.trim()) || name.replace(/^\.\//, "");
  const fetchUrl = sessionArtifactContentUrl(sessionId, rel);
  const preview = artifactPreviewUrl(fetchUrl);
  const download = artifactDownloadUrl(fetchUrl);
  return (
    <div className="flex items-center gap-3 rounded-lg border bg-card px-3 py-2 animate-fade-in">
      <PptxAwareLink
        previewHref={preview}
        fileName={name}
        mime={mime}
        title="点击预览"
        className="flex min-w-0 flex-1 cursor-pointer items-center gap-3 rounded-md text-left no-underline outline-none ring-offset-background transition-colors hover:bg-accent/60 focus-visible:ring-2 focus-visible:ring-ring -m-1 bg-transparent p-1"
      >
        <div className="size-9 shrink-0 rounded-md bg-muted grid place-items-center">
          <FileText className="size-4" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-medium text-foreground underline-offset-2 hover:underline">
            {name}
          </div>
          <div className="truncate text-xs text-muted-foreground">{mime}</div>
        </div>
      </PptxAwareLink>
      <div className="flex shrink-0 items-center gap-1">
        <PptxAwarePreviewButton previewHref={preview} fileName={name} mime={mime}>
          <ExternalLink className="size-3.5" /> 预览
        </PptxAwarePreviewButton>
        <a
          href={download}
          download={name}
          title="下载"
          className="inline-flex size-8 items-center justify-center rounded-md text-foreground transition-colors hover:bg-accent hover:text-accent-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <Download className="size-3.5" />
        </a>
      </div>
    </div>
  );
}
