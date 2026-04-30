import { Download, FileText, ExternalLink } from "lucide-react";

import { Button } from "@/components/ui/button";
import { artifactDownloadUrl, artifactPreviewUrl } from "@/lib/utils/artifact-links";

export function ArtifactCard({ name, mime, url }: { name: string; mime: string; url: string }) {
  const preview = artifactPreviewUrl(url);
  const download = artifactDownloadUrl(url);
  return (
    <div className="flex items-center gap-3 rounded-lg border bg-card px-3 py-2 animate-fade-in">
      <div className="size-9 rounded-md bg-muted grid place-items-center">
        <FileText className="size-4" />
      </div>
      <div className="flex-1 min-w-0">
        <div className="text-sm font-medium truncate">{name}</div>
        <div className="text-xs text-muted-foreground">{mime}</div>
      </div>
      <Button asChild size="sm" variant="outline">
        <a href={preview} target="_blank" rel="noreferrer">
          <ExternalLink className="size-3.5" /> 预览
        </a>
      </Button>
      <Button asChild size="sm" variant="ghost">
        <a href={download} download={name}>
          <Download className="size-3.5" />
        </a>
      </Button>
    </div>
  );
}
