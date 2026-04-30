"use client";

import { useMemo, useState } from "react";
import {
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  Clapperboard,
  Download,
  ExternalLink,
  FileText,
  Image as ImageIcon,
  Music,
  Package,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { useChatStore } from "@/lib/store/chat";
import { useLiveStore } from "@/lib/store/live";
import {
  artifactDownloadUrl,
  artifactPreviewUrl,
  parseArtifactPathFromUrl,
} from "@/lib/utils/artifact-links";
import {
  looksLikeDeliverableFilename,
  sessionArtifactContentUrl,
} from "@/lib/utils/deliverable-resolve";

const PREVIEW_LIMIT = 12;

type HubRow = {
  key: string;
  name: string;
  desc: string;
  mime: string;
  url: string;
  ts: number;
  kind: "artifact" | "path";
};

function normalizePath(p: string): string {
  return p.trim().replace(/^\.\//, "");
}

function friendlyDesc(mime: string, name: string, kind: HubRow["kind"]): string {
  if (kind === "path") return "沙盒路径（轨迹）";
  if (mime.startsWith("video/")) return "视频文件";
  if (mime.startsWith("audio/")) return "音频文件";
  if (mime.startsWith("image/")) return "图像";
  if (mime === "text/html" || /\.html?$/i.test(name)) return "HTML 页面";
  if (/presentation|powerpoint|\.pptx?$/i.test(mime) || /\.pptx?$/i.test(name)) return "演示文稿";
  if (mime === "text/markdown" || /\.md$/i.test(name)) return "Markdown 文档";
  return mime || "文件";
}

function RowIcon({ mime, name }: { mime: string; name: string }) {
  if (mime.startsWith("video/") || /\.mp4$/i.test(name)) {
    return <Clapperboard className="size-4 shrink-0 text-violet-600 dark:text-violet-400" />;
  }
  if (mime.startsWith("audio/") || /\.(mp3|wav|m4a)$/i.test(name)) {
    return <Music className="size-4 shrink-0 text-emerald-600 dark:text-emerald-400" />;
  }
  if (mime.startsWith("image/") || /\.(png|jpe?g|gif|webp)$/i.test(name)) {
    return <ImageIcon className="size-4 shrink-0 text-sky-600 dark:text-sky-400" />;
  }
  return <FileText className="size-4 shrink-0 text-muted-foreground" />;
}

/** live 列表已新→旧；同一路径只保留第一条（最新）。 */
function dedupeLiveArtifacts(
  artifacts: { name: string; description?: string; mime: string; url: string; ts: number }[],
) {
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

function pickFeaturedKey(rows: HubRow[]): string | null {
  const videos = rows.filter(
    (r) => r.mime.startsWith("video/") || /\.mp4$/i.test(r.name),
  );
  if (videos.length === 0) return null;
  let best = videos[0]!;
  let score = -1;
  for (const v of videos) {
    let s = v.ts / 1e12;
    if (/最终|合成|成片|final|composite|merge|talk_show|talkshow|交付/i.test(v.name)) s += 10;
    if (/_show\.mp4$/i.test(v.name) || /final/i.test(v.name)) s += 5;
    if (s > score) {
      score = s;
      best = v;
    }
  }
  return best.key;
}

export function DeliverablesHub({ sessionId }: { sessionId: string }) {
  const artifacts = useLiveStore((s) => s.artifacts);
  const fileItems = useLiveStore((s) => s.fileItems);
  const messages = useChatStore((s) => s.messages);
  const [expanded, setExpanded] = useState(false);

  const rows = useMemo(() => {
    const byKey = new Map<string, HubRow>();

    const put = (key: string, row: HubRow) => {
      const prev = byKey.get(key);
      if (!prev || row.ts >= prev.ts) byKey.set(key, row);
    };

    const dedupedLive = dedupeLiveArtifacts(artifacts);
    const covered = new Set<string>();

    for (const a of dedupedLive) {
      const key = normalizePath(a.description ?? "") || normalizePath(a.name);
      if (!key) continue;
      covered.add(key);
      put(key, {
        key,
        name: a.name,
        desc: friendlyDesc(a.mime, a.name, "artifact"),
        mime: a.mime,
        url: a.url,
        ts: a.ts,
        kind: "artifact",
      });
    }

    for (const m of messages) {
      if (m.kind !== "artifact") continue;
      const path = parseArtifactPathFromUrl(m.url);
      const key = path ? normalizePath(path) : normalizePath(m.name);
      if (!key) continue;
      covered.add(key);
      put(key, {
        key,
        name: m.name,
        desc: friendlyDesc(m.mime, m.name, "artifact"),
        mime: m.mime,
        url: m.url,
        ts: m.createdAt,
        kind: "artifact",
      });
    }

    for (const f of fileItems) {
      const p = normalizePath(f.path);
      if (!p || covered.has(p)) continue;
      if (!looksLikeDeliverableFilename(p)) continue;
      covered.add(p);
      const base = p.split("/").pop() ?? p;
      put(p, {
        key: p,
        name: base,
        desc: friendlyDesc("", base, "path"),
        mime: "",
        url: sessionArtifactContentUrl(sessionId, p),
        ts: f.ts,
        kind: "path",
      });
    }

    const list = Array.from(byKey.values()).sort((a, b) => b.ts - a.ts);
    return list;
  }, [artifacts, fileItems, messages, sessionId]);

  if (rows.length === 0) return null;

  const featured = pickFeaturedKey(rows);
  const shown = expanded ? rows : rows.slice(0, PREVIEW_LIMIT);
  const hasMore = rows.length > PREVIEW_LIMIT;

  return (
    <Card className="border-primary/20 bg-card/95 shadow-sm animate-fade-in">
      <CardContent className="p-3 sm:p-4">
        <div className="mb-2 flex flex-wrap items-center gap-2">
          <Package className="size-4 text-primary" />
          <h3 className="text-sm font-semibold">📁 生成的文件</h3>
          <span className="rounded-full bg-primary/10 px-2 py-0.5 text-[11px] font-medium text-primary tabular-nums">
            {rows.length} 项
          </span>
          <span className="text-[11px] text-muted-foreground">
            本会话汇总 · 随 artifact 与消息同步 · 不依赖模型手写表格
          </span>
        </div>

        <div className="mb-1 text-[11px] text-muted-foreground">
          点击<strong className="font-medium text-foreground">文件名</strong>或
          <strong className="font-medium text-foreground">说明</strong>在新标签页打开预览；「下载」保存到本地。
        </div>

        <div className="overflow-x-auto rounded-md border border-border/80">
          <table className="w-full min-w-[32rem] border-collapse text-sm">
            <thead>
              <tr className="border-b border-border bg-muted/40 text-left text-xs font-medium text-muted-foreground">
                <th className="px-3 py-2">文件</th>
                <th className="px-3 py-2">说明</th>
                <th className="w-[1%] whitespace-nowrap px-3 py-2 text-right">操作</th>
              </tr>
            </thead>
            <tbody>
              {shown.map((r) => {
                const preview = artifactPreviewUrl(r.url);
                const download = artifactDownloadUrl(r.url);
                const isFeatured = featured === r.key;
                return (
                  <tr
                    key={r.key}
                    className={
                      isFeatured
                        ? "border-b border-border/60 bg-emerald-500/5"
                        : "border-b border-border/60"
                    }
                  >
                    <td className="px-3 py-2 align-middle">
                      <a
                        href={preview}
                        target="_blank"
                        rel="noopener noreferrer"
                        title="点击预览"
                        className="group flex max-w-full items-center gap-2 rounded-md px-1 py-1 -mx-1 text-left no-underline outline-none ring-offset-background transition-colors hover:bg-accent/70 focus-visible:ring-2 focus-visible:ring-ring"
                      >
                        <RowIcon mime={r.mime} name={r.name} />
                        <span className="font-mono text-xs font-medium break-all text-foreground underline-offset-2 group-hover:text-primary group-hover:underline">
                          {r.name}
                        </span>
                        {isFeatured ? (
                          <CheckCircle2
                            className="size-4 shrink-0 text-emerald-600 dark:text-emerald-400"
                            aria-label="主推交付"
                          />
                        ) : null}
                      </a>
                    </td>
                    <td className="max-w-[14rem] px-3 py-2 align-middle text-xs">
                      <a
                        href={preview}
                        target="_blank"
                        rel="noopener noreferrer"
                        title="点击预览"
                        className="block rounded-md px-1 py-1 -mx-1 text-muted-foreground no-underline outline-none ring-offset-background transition-colors hover:bg-accent/70 hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring"
                      >
                        {isFeatured ? (
                          <span className="font-medium text-foreground">✅ 最终成片 / 主推交付 · </span>
                        ) : null}
                        <span className="underline-offset-2 hover:underline">{r.desc}</span>
                      </a>
                    </td>
                    <td className="whitespace-nowrap px-3 py-2 text-right align-middle">
                      <div className="inline-flex flex-wrap justify-end gap-1">
                        <Button asChild size="sm" variant="outline" className="h-7 gap-1 px-2 text-xs">
                          <a href={preview} target="_blank" rel="noopener noreferrer">
                            <ExternalLink className="size-3" />
                            预览
                          </a>
                        </Button>
                        <a
                          href={download}
                          download={r.name}
                          className="inline-flex h-7 items-center justify-center gap-1 whitespace-nowrap rounded-md px-2 text-xs font-medium text-foreground transition-colors hover:bg-accent hover:text-accent-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                          title="下载到本地"
                        >
                          <Download className="size-3" />
                          下载
                        </a>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>

        {hasMore ? (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="mt-2 h-8 w-full gap-1 text-xs text-muted-foreground"
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
