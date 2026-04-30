"use client";

import { useMemo, useState } from "react";
import { ChevronDown, ChevronUp, Download, ExternalLink, FolderOpen, Package } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useLiveStore } from "@/lib/store/live";
import { artifactDownloadUrl, artifactPreviewUrl } from "@/lib/utils/artifact-links";
import {
  looksLikeDeliverableFilename,
  sessionArtifactContentUrl,
} from "@/lib/utils/deliverable-resolve";

const PREVIEW_LIMIT = 8;

type HubRow = {
  key: string;
  name: string;
  subtitle: string;
  url: string;
  ts: number;
  kind: "artifact" | "path";
};

function normalizePath(p: string): string {
  return p.trim().replace(/^\.\//, "");
}

/** artifacts 已按时间新→旧；同一路径只保留最新一条。 */
function dedupeArtifactsByPath(
  artifacts: { name: string; description?: string; mime: string; url: string; ts: number }[],
): typeof artifacts {
  const seen = new Set<string>();
  const out: typeof artifacts = [];
  for (const a of artifacts) {
    const key = normalizePath(a.description ?? "") || a.name;
    if (!key || seen.has(key)) continue;
    seen.add(key);
    out.push(a);
  }
  return out;
}

export function DeliverablesHub({ sessionId }: { sessionId: string }) {
  const artifacts = useLiveStore((s) => s.artifacts);
  const fileItems = useLiveStore((s) => s.fileItems);
  const [expanded, setExpanded] = useState(false);

  const rows = useMemo(() => {
    const deduped = dedupeArtifactsByPath(artifacts);
    const covered = new Set(
      deduped.map((a) => normalizePath(a.description ?? "") || a.name).filter(Boolean),
    );
    const list: HubRow[] = deduped.map((a) => ({
      key: `a:${normalizePath(a.description ?? "") || a.name}`,
      name: a.name,
      subtitle: a.mime,
      url: a.url,
      ts: a.ts,
      kind: "artifact" as const,
    }));
    for (const f of fileItems) {
      const p = normalizePath(f.path);
      if (!p || covered.has(p)) continue;
      if (!looksLikeDeliverableFilename(p)) continue;
      covered.add(p);
      const base = p.split("/").pop() ?? p;
      list.push({
        key: `p:${p}`,
        name: base,
        subtitle: f.source === "artifact" ? "artifact" : "sandbox",
        url: sessionArtifactContentUrl(sessionId, p),
        ts: f.ts,
        kind: "path",
      });
    }
    list.sort((x, y) => y.ts - x.ts);
    return list;
  }, [artifacts, fileItems, sessionId]);

  if (rows.length === 0) return null;

  const shown = expanded ? rows : rows.slice(0, PREVIEW_LIMIT);
  const hasMore = rows.length > PREVIEW_LIMIT;

  return (
    <Card className="mb-4 border-primary/15 bg-background/90 shadow-sm animate-fade-in">
      <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
        <div className="flex items-center gap-2">
          <Package className="size-4 text-primary" />
          <CardTitle className="text-sm font-semibold">当前交付物</CardTitle>
          <span className="rounded-full bg-muted px-2 py-0.5 text-[11px] text-muted-foreground tabular-nums">
            {rows.length}
          </span>
        </div>
        <span className="text-[11px] text-muted-foreground">本会话 · 随执行更新</span>
      </CardHeader>
      <CardContent className="space-y-2 pt-0">
        <p className="text-[11px] leading-relaxed text-muted-foreground">
          与聊天里模型手写的清单无关；数据来自本会话已确认的产出（artifact）及沙盒内可预览路径。
        </p>
        <ul className="space-y-2">
          {shown.map((r) => {
            const preview = artifactPreviewUrl(r.url);
            const download = artifactDownloadUrl(r.url);
            return (
              <li
                key={r.key}
                className="flex flex-col gap-2 rounded-lg border border-border/70 bg-card/50 px-3 py-2 sm:flex-row sm:items-center sm:gap-3"
              >
                <div className="flex min-w-0 flex-1 items-start gap-2">
                  <FolderOpen className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
                  <div className="min-w-0">
                    <div className="truncate text-sm font-medium">{r.name}</div>
                    <div className="truncate text-[11px] text-muted-foreground">{r.subtitle}</div>
                  </div>
                </div>
                <div className="flex shrink-0 flex-wrap items-center gap-1 sm:justify-end">
                  <Button asChild size="sm" variant="outline" className="h-7 gap-1 px-2 text-xs">
                    <a href={preview} target="_blank" rel="noopener noreferrer">
                      <ExternalLink className="size-3" />
                      预览
                    </a>
                  </Button>
                  <Button asChild size="sm" variant="ghost" className="h-7 gap-1 px-2 text-xs">
                    <a href={download} download={r.name}>
                      <Download className="size-3" />
                      下载
                    </a>
                  </Button>
                </div>
              </li>
            );
          })}
        </ul>
        {hasMore ? (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="h-8 w-full gap-1 text-xs text-muted-foreground"
            onClick={() => setExpanded((e) => !e)}
          >
            {expanded ? (
              <>
                <ChevronUp className="size-3.5" />
                收起
              </>
            ) : (
              <>
                <ChevronDown className="size-3.5" />
                查看全部（{rows.length}）
              </>
            )}
          </Button>
        ) : null}
      </CardContent>
    </Card>
  );
}
