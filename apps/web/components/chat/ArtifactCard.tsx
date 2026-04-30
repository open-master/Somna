import { Download, FileText, ExternalLink } from "lucide-react";

import { Button } from "@/components/ui/button";
import { artifactDownloadUrl, artifactPreviewUrl } from "@/lib/utils/artifact-links";

export function ArtifactCard({ name, mime, url }: { name: string; mime: string; url: string }) {
  const preview = artifactPreviewUrl(url);
  const download = artifactDownloadUrl(url);
  return (
    <div className="flex items-center gap-3 rounded-lg border bg-card px-3 py-2 animate-fade-in">
      <a
        href={preview}
        target="_blank"
        rel="noopener noreferrer"
        title="点击预览"
        className="flex min-w-0 flex-1 items-center gap-3 rounded-md no-underline outline-none ring-offset-background transition-colors hover:bg-accent/60 focus-visible:ring-2 focus-visible:ring-ring -m-1 p-1"
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
      </a>
      <div className="flex shrink-0 items-center gap-1">
        <Button asChild size="sm" variant="outline">
          <a href={preview} target="_blank" rel="noopener noreferrer">
            <ExternalLink className="size-3.5" /> 预览
          </a>
        </Button>
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
