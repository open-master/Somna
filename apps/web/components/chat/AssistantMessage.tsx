"use client";

import React, { createContext, useContext, useMemo } from "react";
import ReactMarkdown from "react-markdown";
import rehypeRaw from "rehype-raw";
import rehypeSanitize from "rehype-sanitize";
import remarkGfm from "remark-gfm";
import { Download, ExternalLink, Sparkles } from "lucide-react";

import { PptxAwareLink, PptxAwarePreviewButton } from "@/components/chat/PptxAwarePreview";
import { Card, CardContent } from "@/components/ui/card";
import { cn } from "@/lib/utils/cn";
import { useLiveStore } from "@/lib/store/live";
import { artifactDownloadUrl, artifactPreviewUrl, parseArtifactPathFromUrl } from "@/lib/utils/artifact-links";
import {
  resolveDeliverableUrl,
  tableHeaderLooksLikeFileManifest,
} from "@/lib/utils/deliverable-resolve";

const DeliverablesTableContext = createContext(false);

function collectPlainText(node: React.ReactNode): string {
  if (node == null || typeof node === "boolean") return "";
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(collectPlainText).join("");
  if (React.isValidElement(node)) {
    const ch = (node.props as { children?: React.ReactNode }).children;
    return collectPlainText(ch);
  }
  return "";
}

function rowUsesThOnly(children: React.ReactNode): boolean {
  const cells = React.Children.toArray(children);
  return (
    cells.length > 0 && cells.every((c) => React.isValidElement(c) && isThCell(c))
  );
}

function isThCell(el: React.ReactElement): boolean {
  const t = el.type;
  return t === "th" || (typeof t === "string" && t.toLowerCase() === "th");
}

/** 深度遍历 thead/tbody/tfoot，收集所有 tr（兼容 Markdown GFM 与纯 HTML 表格）。 */
function flattenTableRows(tableChildren: React.ReactNode): React.ReactElement[] {
  const rows: React.ReactElement[] = [];
  const walk = (node: React.ReactNode) => {
    React.Children.forEach(node, (child) => {
      if (!React.isValidElement(child)) return;
      const t = child.type;
      const tag = typeof t === "string" ? t.toLowerCase() : "";
      if (tag === "tr") {
        rows.push(child);
        return;
      }
      if (["thead", "tbody", "tfoot"].includes(tag)) {
        walk((child.props as { children?: React.ReactNode }).children);
      }
    });
  };
  walk(tableChildren);
  return rows;
}

function isDeliverablesMarkdownTable(children: React.ReactNode): boolean {
  for (const tr of flattenTableRows(children)) {
    const ch = (tr.props as { children?: React.ReactNode }).children;
    if (!rowUsesThOnly(ch)) continue;
    const text = collectPlainText(ch);
    if (tableHeaderLooksLikeFileManifest(text)) return true;
  }
  return false;
}

function DeliverablesDataRow({
  sessionId,
  children,
}: {
  sessionId: string;
  children: React.ReactNode;
}) {
  const artifacts = useLiveStore((s) => s.artifacts);
  const fileItems = useLiveStore((s) => s.fileItems);
  const cells = React.Children.toArray(children);
  const firstPlain = cells[0] ? collectPlainText(cells[0]) : "";
  const url = useMemo(
    () => resolveDeliverableUrl(sessionId, firstPlain, artifacts, fileItems),
    [sessionId, firstPlain, artifacts, fileItems],
  );
  const downloadName =
    parseArtifactPathFromUrl(url ?? "")?.split("/").pop() ||
    firstPlain.match(/[\w.*-]+\.(?:mp4|webm|png|jpe?g|gif|webp|md|pdf|html?|pptx?|mp3|wav)/i)?.[0] ||
    "download";
  const previewUrl = url ? artifactPreviewUrl(url) : "";

  return (
    <tr>
      {cells.map((cell, i) => {
        if (i === 0 && url && React.isValidElement(cell)) {
          const props = cell.props as React.TdHTMLAttributes<HTMLTableCellElement> & {
            children?: React.ReactNode;
          };
          return (
            <td key={cell.key ?? "c0"} {...props} className={cn(props.className)}>
              <PptxAwareLink
                previewHref={previewUrl}
                fileName={downloadName}
                mime=""
                title="点击预览"
                className="block w-full cursor-pointer rounded-sm px-1 py-0.5 -mx-1 text-left text-inherit no-underline outline-none ring-offset-background hover:bg-accent/70 hover:underline focus-visible:ring-2 focus-visible:ring-ring"
              >
                {props.children}
              </PptxAwareLink>
            </td>
          );
        }
        if (i === 1 && url && React.isValidElement(cell)) {
          const props = cell.props as React.TdHTMLAttributes<HTMLTableCellElement> & {
            children?: React.ReactNode;
          };
          return (
            <td key={cell.key ?? "c1"} {...props} className={cn(props.className)}>
              <PptxAwareLink
                previewHref={previewUrl}
                fileName={downloadName}
                mime=""
                title="点击预览"
                className="block w-full cursor-pointer rounded-sm px-1 py-0.5 -mx-1 text-left text-inherit no-underline outline-none ring-offset-background hover:bg-accent/70 hover:underline focus-visible:ring-2 focus-visible:ring-ring"
              >
                {props.children}
              </PptxAwareLink>
            </td>
          );
        }
        return cell;
      })}
      <td className="align-middle whitespace-nowrap text-right not-prose border-l border-border/70">
        {url ? (
          <span className="inline-flex flex-wrap items-center justify-end gap-1 px-1">
            <PptxAwarePreviewButton
              previewHref={previewUrl}
              fileName={downloadName}
              mime=""
              className="h-7 gap-1 px-2 text-xs"
            >
              <ExternalLink className="size-3" />
              预览
            </PptxAwarePreviewButton>
            <a
              href={artifactDownloadUrl(url)}
              download={downloadName}
              className="inline-flex h-7 items-center justify-center gap-1 whitespace-nowrap rounded-md px-2 text-xs font-medium transition-colors hover:bg-accent hover:text-accent-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              title="下载"
            >
              <Download className="size-3" />
              下载
            </a>
          </span>
        ) : (
          <span className="pr-2 text-xs text-muted-foreground">—</span>
        )}
      </td>
    </tr>
  );
}

function MarkdownTableRow({ sessionId, children }: { sessionId: string; children: React.ReactNode }) {
  const isDelivTable = useContext(DeliverablesTableContext);
  if (!isDelivTable) {
    return <tr>{children}</tr>;
  }
  if (rowUsesThOnly(children)) {
    return (
      <tr>
        {children}
        <th className="border-b border-border/80 bg-muted/40 px-2 py-2 text-right font-medium not-prose">
          操作
        </th>
      </tr>
    );
  }
  return <DeliverablesDataRow sessionId={sessionId}>{children}</DeliverablesDataRow>;
}

export function AssistantMessage({
  text,
  thinking,
  sessionId,
}: {
  text: string;
  thinking?: string;
  sessionId: string;
}) {
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
          <Card className="border-border/70 bg-gradient-to-b from-card to-muted/10 shadow-sm">
            <CardContent className="p-4 sm:p-5">
              <article className="prose prose-sm max-w-none dark:prose-invert prose-p:my-1.5 prose-pre:my-2 prose-headings:scroll-mt-20 prose-a:text-primary prose-a:underline-offset-4 hover:prose-a:underline">
                <ReactMarkdown
                  remarkPlugins={[remarkGfm]}
                  rehypePlugins={[rehypeRaw, rehypeSanitize]}
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
                table: ({ children }) => {
                  const isDeliv = isDeliverablesMarkdownTable(children);
                  return (
                    <DeliverablesTableContext.Provider value={isDeliv}>
                      <div className="not-prose my-3 overflow-hidden rounded-lg border border-border/80 bg-card shadow-sm">
                        <div className="overflow-x-auto">
                          <table className="w-full min-w-[28rem] border-collapse text-sm text-foreground">
                            {children}
                          </table>
                        </div>
                      </div>
                    </DeliverablesTableContext.Provider>
                  );
                },
                thead: ({ children }) => <thead className="bg-muted/55">{children}</thead>,
                tbody: ({ children }) => <tbody className="[&_tr:last-child_td]:border-b-0">{children}</tbody>,
                th: ({ children, className, ...props }) => (
                  <th className={cn("border-b border-border px-3 py-2.5 text-left text-xs font-semibold", className)} {...props}>
                    {children}
                  </th>
                ),
                td: ({ children, className, ...props }) => (
                  <td className={cn("border-b border-border/60 px-3 py-2.5 align-top text-sm leading-relaxed", className)} {...props}>
                    {children}
                  </td>
                ),
                tr: ({ children }) => <MarkdownTableRow sessionId={sessionId}>{children}</MarkdownTableRow>,
                  }}
                >
                  {text}
                </ReactMarkdown>
              </article>
            </CardContent>
          </Card>
        ) : (
          <div className="text-xs text-muted-foreground italic">思考中...</div>
        )}
      </div>
    </div>
  );
}
