"use client";

import * as Dialog from "@radix-ui/react-dialog";
import { useState } from "react";
import { X } from "lucide-react";

import { Button } from "@/components/ui/button";
import type { SessionAttachmentRef } from "@/lib/api/sessions";
import { sessionUserUploadFileUrl } from "@/lib/api/sessions";
import { cn } from "@/lib/utils/cn";

function isPreviewableImage(a: SessionAttachmentRef): boolean {
  if (a.mime.startsWith("image/")) return true;
  return /\.(png|jpe?g|gif|webp)$/i.test(a.filename);
}

function AttachmentPreviewBody({
  url,
  filename,
  mime,
}: {
  url: string;
  filename: string;
  mime: string;
}) {
  const lower = filename.toLowerCase();
  const isImg = mime.startsWith("image/") || /\.(png|jpe?g|gif|webp|svg)$/i.test(lower);
  const pdf = mime === "application/pdf" || lower.endsWith(".pdf");
  const textish = mime.startsWith("text/") || /\.(txt|md|csv|json|xml|yaml|yml|log)$/i.test(lower);

  if (isImg) {
    return (
      // eslint-disable-next-line @next/next/no-img-element -- 同源鉴权 URL
      <img
        src={url}
        alt={filename}
        className="max-h-[min(85vh,900px)] w-auto max-w-full rounded-lg object-contain"
      />
    );
  }
  if (pdf) {
    return (
      <iframe
        title={filename}
        src={url}
        className="h-[min(85vh,800px)] w-full rounded-lg border border-border bg-muted/20"
      />
    );
  }
  if (textish) {
    return (
      <iframe
        title={filename}
        src={url}
        className="h-[min(85vh,800px)] w-full rounded-lg border border-border bg-background"
      />
    );
  }
  return (
    <div className="flex flex-col items-center gap-4 py-10 text-center">
      <p className="text-sm text-muted-foreground">此文件类型无法在页面内嵌预览。</p>
      <Button asChild className="rounded-xl">
        <a href={url} target="_blank" rel="noopener noreferrer">
          在新标签页打开
        </a>
      </Button>
    </div>
  );
}

function UserAttachmentBlock({
  sessionId,
  attachment: a,
  onOpenPreview,
}: {
  sessionId: string;
  attachment: SessionAttachmentRef;
  onOpenPreview: (url: string, filename: string, mime: string) => void;
}) {
  const [broken, setBroken] = useState(false);
  const showImg = isPreviewableImage(a) && !broken;
  const url = sessionUserUploadFileUrl(sessionId, a.s3_key);
  const open = () => onOpenPreview(url, a.filename, a.mime);

  if (!showImg) {
    return (
      <button
        type="button"
        onClick={open}
        className={cn(
          "max-w-full cursor-pointer rounded-md border border-primary/30 bg-primary/10 px-2 py-0.5 text-left text-[11px] text-primary",
          "transition-colors hover:bg-primary/15 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        )}
        title={`预览 ${a.filename}`}
      >
        <span className="truncate">{a.filename}</span>
      </button>
    );
  }

  return (
    <button
      type="button"
      onClick={open}
      className="group flex max-w-[min(100%,24rem)] cursor-pointer flex-col items-end gap-0.5 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 rounded-lg"
      title={`预览 ${a.filename}`}
    >
      {/* eslint-disable-next-line @next/next/no-img-element -- 同源鉴权 URL，不做 next/image 域名配置 */}
      <img
        src={url}
        alt={a.filename}
        loading="lazy"
        className="max-h-52 w-auto rounded-lg border border-primary/25 object-contain shadow-sm transition-[box-shadow] group-hover:border-primary/50 group-hover:shadow-md dark:border-primary/40"
        onError={() => setBroken(true)}
      />
      <span className="truncate text-[10px] text-primary/90 underline-offset-2 group-hover:underline">
        {a.filename}
      </span>
    </button>
  );
}

export function UserMessage({
  sessionId,
  text,
  attachments,
}: {
  sessionId: string;
  text: string;
  attachments?: SessionAttachmentRef[];
}) {
  const [preview, setPreview] = useState<{ url: string; filename: string; mime: string } | null>(null);

  return (
    <>
      <div className="flex flex-col items-end gap-2 animate-fade-in">
        <div className="max-w-[75%] rounded-2xl rounded-tr-sm bg-primary px-4 py-2 text-sm text-primary-foreground whitespace-pre-wrap">
          {text}
        </div>
        {attachments?.length ? (
          <div className="flex max-w-[75%] flex-col items-end gap-2">
            {attachments.map((a) => (
              <UserAttachmentBlock
                key={a.s3_key}
                sessionId={sessionId}
                attachment={a}
                onOpenPreview={(url, filename, mime) => setPreview({ url, filename, mime })}
              />
            ))}
          </div>
        ) : null}
      </div>

      <Dialog.Root open={preview !== null} onOpenChange={(o) => !o && setPreview(null)}>
        <Dialog.Portal>
          <Dialog.Overlay className="fixed inset-0 z-[100] bg-background/80 backdrop-blur-sm data-[state=open]:animate-in data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0" />
          <Dialog.Content className="fixed left-1/2 top-1/2 z-[100] flex max-h-[min(94vh,960px)] w-[min(96vw,56rem)] -translate-x-1/2 -translate-y-1/2 flex-col gap-3 rounded-2xl border bg-card p-4 shadow-xl outline-none data-[state=open]:animate-in data-[state=closed]:zoom-out-95 data-[state=open]:zoom-in-95">
            <div className="flex shrink-0 items-start justify-between gap-3">
              <Dialog.Title className="min-w-0 flex-1 truncate pr-2 text-sm font-semibold" title={preview?.filename}>
                {preview?.filename ?? "附件预览"}
              </Dialog.Title>
              <Dialog.Close asChild>
                <Button type="button" size="icon" variant="ghost" className="size-8 shrink-0 rounded-lg" aria-label="关闭">
                  <X className="size-4" />
                </Button>
              </Dialog.Close>
            </div>
            <div className="min-h-0 flex-1 overflow-auto">
              {preview ? (
                <AttachmentPreviewBody url={preview.url} filename={preview.filename} mime={preview.mime} />
              ) : null}
            </div>
            <div className="flex shrink-0 flex-wrap justify-end gap-2 border-t pt-3">
              {preview ? (
                <Button asChild variant="outline" size="sm" className="rounded-xl">
                  <a href={preview.url} target="_blank" rel="noopener noreferrer">
                    新标签页打开
                  </a>
                </Button>
              ) : null}
              <Dialog.Close asChild>
                <Button type="button" size="sm" variant="secondary" className="rounded-xl">
                  关闭
                </Button>
              </Dialog.Close>
            </div>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </>
  );
}
