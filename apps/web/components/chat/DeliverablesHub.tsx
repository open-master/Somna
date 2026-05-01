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
import { PptxAwareLink, PptxAwarePreviewAnchor } from "@/components/chat/PptxAwarePreview";
import { useChatStore } from "@/lib/store/chat";
import { useLiveStore } from "@/lib/store/live";
import { cn } from "@/lib/utils/cn";
import {
  artifactDownloadUrl,
  artifactPreviewUrl,
  parseArtifactPathFromUrl,
} from "@/lib/utils/artifact-links";
import {
  looksLikeDeliverableFilename,
  sessionArtifactContentUrl,
} from "@/lib/utils/deliverable-resolve";
import { normalizeWorkspacePath } from "@/lib/utils/workspace-path";

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
  return normalizeWorkspacePath(p.trim().replace(/^\.\//, ""));
}

/** 与列表 dedupe 键一致：避免 index.html 与 foo/index.html 在 artifact / fileItems 中各记一条却显示重名。 */
function hubDedupeKey(pathOrUrl: string, fallbackName?: string): string {
  const fromUrl = parseArtifactPathFromUrl(pathOrUrl);
  const raw = (fromUrl ?? pathOrUrl ?? fallbackName ?? "").trim();
  if (!raw) return "";
  return normalizePath(raw);
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

/** 与后端 _artifact_path_candidates 对齐：优先 description，其次 URL 的 path=，最后 basename。 */
function artifactRelPath(a: { name: string; description?: string; url: string }): string {
  const d = normalizePath(a.description ?? "");
  if (d) return d;
  const fromUrl = parseArtifactPathFromUrl(a.url);
  if (fromUrl) return normalizePath(fromUrl);
  return normalizePath(a.name);
}

/** live 列表已新→旧；同一沙盒相对路径只保留一条（键与 Hub 一致）。 */
function dedupeLiveArtifacts(
  artifacts: { name: string; description?: string; mime: string; url: string; ts: number }[],
) {
  const seen = new Set<string>();
  const out: typeof artifacts = [];
  for (const a of artifacts) {
    const key = hubDedupeKey(artifactRelPath(a), a.name);
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
      const rel = artifactRelPath(a);
      const key = hubDedupeKey(rel, a.name);
      if (!key) continue;
      covered.add(key);
      put(key, {
        key,
        name: a.name?.includes("/") ? (key.split("/").pop() ?? a.name) : a.name,
        desc: friendlyDesc(a.mime, a.name, "artifact"),
        mime: a.mime,
        url: sessionArtifactContentUrl(sessionId, key),
        ts: a.ts,
        kind: "artifact",
      });
    }

    for (const m of messages) {
      if (m.kind !== "artifact") continue;
      const pathFromUrl = parseArtifactPathFromUrl(m.url);
      const raw = (pathFromUrl ? normalizePath(pathFromUrl) : "") || normalizePath(m.name);
      const key = hubDedupeKey(raw, m.name);
      if (!key) continue;
      covered.add(key);
      put(key, {
        key,
        name: key.includes("/") ? (key.split("/").pop() ?? m.name) : m.name,
        desc: friendlyDesc(m.mime, m.name, "artifact"),
        mime: m.mime,
        url: sessionArtifactContentUrl(sessionId, key),
        ts: m.createdAt,
        kind: "artifact",
      });
    }

    for (const f of fileItems) {
      const p = normalizePath(f.path);
      if (!p) continue;
      const key = hubDedupeKey(p);
      if (!key || covered.has(key)) continue;
      if (!looksLikeDeliverableFilename(p)) continue;
      covered.add(key);
      const base = p.split("/").pop() ?? p;
      put(key, {
        key,
        name: base,
        desc: friendlyDesc("", base, "path"),
        mime: "",
        url: sessionArtifactContentUrl(sessionId, key),
        ts: f.ts,
        kind: "path",
      });
    }

    const list = Array.from(byKey.values()).sort((a, b) => b.ts - a.ts);
    return list;
  }, [artifacts, fileItems, messages, sessionId]);

  const featured = pickFeaturedKey(rows);
  const shown = expanded ? rows : rows.slice(0, PREVIEW_LIMIT);
  const hasMore = rows.length > PREVIEW_LIMIT;

  return (
    <details className="relative z-10 mx-auto w-full max-w-3xl rounded-lg border border-primary/20 bg-card/95 px-3 py-2 shadow-sm animate-fade-in group">
      <summary
        className={cn(
          "flex cursor-pointer list-none flex-wrap items-center gap-2 text-sm font-medium select-none",
          "[&::-webkit-details-marker]:hidden",
        )}
      >
        <Package className="size-4 shrink-0 text-primary" />
        <span>📁 生成的文件</span>
        <span className="rounded-full bg-primary/10 px-2 py-0.5 text-[11px] font-medium text-primary tabular-nums">
          {rows.length} 项
        </span>
        <span className="text-xs text-muted-foreground max-sm:hidden">点击展开 · 收起可留出聊天/执行过程</span>
        <ChevronDown className="ml-auto size-4 shrink-0 text-muted-foreground transition-transform group-open:rotate-180" />
      </summary>

      <div className="mt-2 space-y-2">
        {rows.length === 0 ? (
          <p className="rounded-md border border-dashed bg-muted/20 px-3 py-4 text-xs leading-relaxed text-muted-foreground">
            暂无已索引的交付物（或事件回放尚未到达）。右侧「文件」里若有
            artifact/轨迹，任务结束后一般会列出；若长时间仍为空，请刷新页面以重放事件。
          </p>
        ) : (
          <>
            <p className="text-[11px] text-muted-foreground">
              本会话汇总 · 随 artifact 与消息同步。点击<strong className="font-medium text-foreground">文件名</strong>
              或<strong className="font-medium text-foreground">说明</strong>预览；「下载」保存到本地。
            </p>

            <div className="max-h-[min(50vh,22rem)] overflow-auto rounded-md border border-border/80 overscroll-y-contain">
              <table className="w-full min-w-[32rem] border-collapse text-sm">
                <thead>
                  <tr className="sticky top-0 z-[1] border-b border-border bg-muted/95 text-left text-xs font-medium text-muted-foreground backdrop-blur-sm supports-[backdrop-filter]:bg-muted/80">
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
                          <PptxAwareLink
                            previewHref={preview}
                            fileName={r.name}
                            mime={r.mime}
                            title="点击预览"
                            className="group flex max-w-full cursor-pointer items-center gap-2 rounded-md px-1 py-1 -mx-1 text-left no-underline outline-none ring-offset-background transition-colors hover:bg-accent/70 focus-visible:ring-2 focus-visible:ring-ring"
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
                          </PptxAwareLink>
                        </td>
                        <td className="max-w-[14rem] px-3 py-2 align-middle text-xs">
                          <PptxAwareLink
                            previewHref={preview}
                            fileName={r.name}
                            mime={r.mime}
                            title="点击预览"
                            className="block w-full cursor-pointer rounded-md px-1 py-1 -mx-1 text-left text-muted-foreground no-underline outline-none ring-offset-background transition-colors hover:bg-accent/70 hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring"
                          >
                            {isFeatured ? (
                              <span className="font-medium text-foreground">✅ 最终成片 / 主推交付 · </span>
                            ) : null}
                            <span className="underline-offset-2 hover:underline">{r.desc}</span>
                          </PptxAwareLink>
                        </td>
                        <td className="whitespace-nowrap px-3 py-2 text-right align-middle">
                          <div className="inline-flex flex-wrap justify-end gap-1">
                            <PptxAwarePreviewAnchor
                              previewHref={preview}
                              fileName={r.name}
                              mime={r.mime}
                              className="h-7 cursor-pointer gap-1 px-2 text-xs no-underline"
                            >
                              <ExternalLink className="size-3 shrink-0" />
                              预览
                            </PptxAwarePreviewAnchor>
                            <a
                              href={download}
                              download={r.name}
                              className="inline-flex h-7 cursor-pointer items-center justify-center gap-1 whitespace-nowrap rounded-md px-2 text-xs font-medium text-foreground transition-colors hover:bg-accent hover:text-accent-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
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
                className="h-8 w-full gap-1 text-xs text-muted-foreground"
                onClick={() => setExpanded((e) => !e)}
              >
                {expanded ? (
                  <>
                    <ChevronUp className="size-3.5" />
                    收起列表
                  </>
                ) : (
                  <>
                    <ChevronDown className="size-3.5" />
                    查看全部（{rows.length}）
                  </>
                )}
              </Button>
            ) : null}
          </>
        )}
      </div>
    </details>
  );
}
