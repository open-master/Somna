"use client";

import { useState, type ReactNode } from "react";

import { Button, buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils/cn";
import { isPptxLike } from "@/lib/utils/pptx-detect";

import { PptxPreviewDialog } from "./PptxPreviewDialog";

export function PptxAwareLink({
  previewHref,
  fileName,
  mime,
  className,
  title = "点击预览",
  children,
}: {
  previewHref: string;
  fileName: string;
  mime: string;
  className?: string;
  title?: string;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  if (isPptxLike(fileName, mime)) {
    return (
      <>
        <PptxPreviewDialog open={open} onOpenChange={setOpen} previewUrl={previewHref} title={fileName} />
        <button type="button" title={title} className={cn(className)} onClick={() => setOpen(true)}>
          {children}
        </button>
      </>
    );
  }
  return (
    <a href={previewHref} target="_blank" rel="noopener noreferrer" title={title} className={className}>
      {children}
    </a>
  );
}

export function PptxAwarePreviewButton({
  previewHref,
  fileName,
  mime,
  className,
  children,
}: {
  previewHref: string;
  fileName: string;
  mime: string;
  className?: string;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  if (isPptxLike(fileName, mime)) {
    return (
      <>
        <PptxPreviewDialog open={open} onOpenChange={setOpen} previewUrl={previewHref} title={fileName} />
        <Button type="button" variant="outline" size="sm" className={className} onClick={() => setOpen(true)}>
          {children}
        </Button>
      </>
    );
  }
  return (
    <Button asChild variant="outline" size="sm" className={className}>
      <a href={previewHref} target="_blank" rel="noopener noreferrer">
        {children}
      </a>
    </Button>
  );
}

/** 与 DeliverablesHub 中 `buttonVariants` 拼出的「预览」外观一致 */
export function PptxAwarePreviewAnchor({
  previewHref,
  fileName,
  mime,
  className,
  children,
}: {
  previewHref: string;
  fileName: string;
  mime: string;
  className?: string;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const combo = cn(buttonVariants({ variant: "outline", size: "sm" }), className);
  if (isPptxLike(fileName, mime)) {
    return (
      <>
        <PptxPreviewDialog open={open} onOpenChange={setOpen} previewUrl={previewHref} title={fileName} />
        <button type="button" className={combo} onClick={() => setOpen(true)}>
          {children}
        </button>
      </>
    );
  }
  return (
    <a href={previewHref} target="_blank" rel="noopener noreferrer" className={combo}>
      {children}
    </a>
  );
}
