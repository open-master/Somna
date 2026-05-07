"use client";

import { useState } from "react";

import type { SessionAttachmentRef } from "@/lib/api/sessions";
import { sessionUserUploadFileUrl } from "@/lib/api/sessions";

function isPreviewableImage(a: SessionAttachmentRef): boolean {
  if (a.mime.startsWith("image/")) return true;
  return /\.(png|jpe?g|gif|webp)$/i.test(a.filename);
}

function UserAttachmentBlock({
  sessionId,
  attachment: a,
}: {
  sessionId: string;
  attachment: SessionAttachmentRef;
}) {
  const [broken, setBroken] = useState(false);
  const showImg = isPreviewableImage(a) && !broken;
  const url = sessionUserUploadFileUrl(sessionId, a.s3_key);

  if (!showImg) {
    return (
      <span
        className="rounded-md border border-primary/30 bg-primary/10 px-2 py-0.5 text-[11px] text-primary"
        title={a.filename}
      >
        {a.filename}
      </span>
    );
  }

  return (
    <div className="flex max-w-[min(100%,24rem)] flex-col items-end gap-0.5">
      {/* eslint-disable-next-line @next/next/no-img-element -- 同源鉴权 URL，不做 next/image 域名配置 */}
      <img
        src={url}
        alt={a.filename}
        loading="lazy"
        className="max-h-52 w-auto rounded-lg border border-primary/25 object-contain shadow-sm dark:border-primary/40"
        onError={() => setBroken(true)}
      />
      <span className="truncate text-[10px] text-primary/90">{a.filename}</span>
    </div>
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
  return (
    <div className="flex flex-col items-end gap-2 animate-fade-in">
      <div className="max-w-[75%] rounded-2xl rounded-tr-sm bg-primary px-4 py-2 text-sm text-primary-foreground whitespace-pre-wrap">
        {text}
      </div>
      {attachments?.length ? (
        <div className="flex max-w-[75%] flex-col items-end gap-2">
          {attachments.map((a) => (
            <UserAttachmentBlock key={a.s3_key} sessionId={sessionId} attachment={a} />
          ))}
        </div>
      ) : null}
    </div>
  );
}
