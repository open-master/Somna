"use client";

import { useEffect, useRef, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { ChevronLeft, ChevronRight, Loader2, Pause, Play, X } from "lucide-react";

import { parsePptxToSlides, revokePptxSlideUrls, type PptxSlideModel } from "@/lib/utils/parse-pptx-browser";

const AUTOPLAY_MS = 6000;

export function PptxPreviewDialog({
  open,
  onOpenChange,
  previewUrl,
  title,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  previewUrl: string;
  title: string;
}) {
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [slides, setSlides] = useState<PptxSlideModel[]>([]);
  const [slideIdx, setSlideIdx] = useState(0);
  const [playing, setPlaying] = useState(false);
  const slidesRef = useRef<PptxSlideModel[]>([]);

  slidesRef.current = slides;

  useEffect(() => {
    if (!open) {
      setPlaying(false);
      setSlideIdx(0);
      setLoading(false);
      setErr(null);
      setSlides((prev) => {
        revokePptxSlideUrls(prev);
        return [];
      });
      return;
    }

    let cancelled = false;
    setLoading(true);
    setErr(null);
    setSlides([]);
    setSlideIdx(0);

    (async () => {
      try {
        const res = await fetch(previewUrl, { credentials: "include" });
        if (!res.ok) throw new Error(`加载失败（${res.status}）`);
        const buf = await res.arrayBuffer();
        const parsed = await parsePptxToSlides(buf);
        if (cancelled) {
          revokePptxSlideUrls(parsed);
          return;
        }
        setSlides(parsed);
      } catch (e) {
        if (!cancelled) {
          setErr(e instanceof Error ? e.message : "加载失败");
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [open, previewUrl]);

  useEffect(() => {
    if (!open || !playing || slides.length === 0) return;
    const id = window.setInterval(() => {
      setSlideIdx((i) => (i + 1) % slidesRef.current.length);
    }, AUTOPLAY_MS);
    return () => clearInterval(id);
  }, [open, playing, slides.length]);

  const slide = slides[slideIdx] ?? null;

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-[100] bg-black/50 backdrop-blur-[2px]" />
        <Dialog.Content
          className="fixed left-1/2 top-1/2 z-[101] flex max-h-[90vh] w-[min(920px,calc(100vw-1.5rem))] -translate-x-1/2 -translate-y-1/2 flex-col overflow-hidden rounded-xl border bg-card shadow-lg outline-none"
          onOpenAutoFocus={(e) => e.preventDefault()}
        >
          <div className="flex shrink-0 items-start justify-between gap-3 border-b px-4 py-3">
            <div className="min-w-0">
              <Dialog.Title className="truncate text-sm font-semibold leading-tight">{title}</Dialog.Title>
              <Dialog.Description className="sr-only">
                在当前页面简版预览 PowerPoint 文本与嵌入图片，可翻页或自动播放。
              </Dialog.Description>
              <p className="mt-0.5 text-[11px] text-muted-foreground">
                简版预览 · 复杂排版或视频音频请以「下载」后用 Office 打开
              </p>
            </div>
            <Dialog.Close
              type="button"
              className="rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
              aria-label="关闭"
            >
              <X className="size-4" />
            </Dialog.Close>
          </div>

          <div className="min-h-0 flex-1 overflow-hidden bg-muted/30 p-4">
            {loading ? (
              <div className="flex h-[min(60vh,420px)] flex-col items-center justify-center gap-2 text-sm text-muted-foreground">
                <Loader2 className="size-8 animate-spin text-primary" />
                正在解析幻灯片…
              </div>
            ) : err ? (
              <div className="flex h-[min(60vh,420px)] items-center justify-center px-4 text-center text-sm text-destructive">
                {err}
              </div>
            ) : slide ? (
              <div className="mx-auto flex h-[min(60vh,480px)] max-w-3xl flex-col overflow-hidden rounded-lg border bg-background shadow-sm">
                <div className="min-h-0 flex-1 overflow-y-auto p-6">
                  {slide.texts.length === 0 && slide.imageUrls.length === 0 ? (
                    <p className="text-center text-sm text-muted-foreground">本页无文本与可展示图片</p>
                  ) : (
                    <div className="space-y-5">
                      {slide.texts.length > 0 ? (
                        <div className="space-y-3 text-sm leading-relaxed text-foreground">
                          {slide.texts.map((t, i) => (
                            <p key={i} className="whitespace-pre-wrap">
                              {t}
                            </p>
                          ))}
                        </div>
                      ) : null}
                      {slide.imageUrls.length > 0 ? (
                        <div className="flex flex-col gap-3">
                          {slide.imageUrls.map((src, i) => (
                            <img
                              key={i}
                              src={src}
                              alt=""
                              className="mx-auto max-h-[40vh] w-auto max-w-full object-contain"
                            />
                          ))}
                        </div>
                      ) : null}
                    </div>
                  )}
                </div>
              </div>
            ) : null}
          </div>

          {!loading && !err && slides.length > 0 ? (
            <div className="flex shrink-0 flex-wrap items-center justify-between gap-2 border-t bg-muted/20 px-3 py-2">
              <div className="flex items-center gap-1">
                <button
                  type="button"
                  className="inline-flex size-8 items-center justify-center rounded-md border bg-background text-foreground transition-colors hover:bg-muted disabled:pointer-events-none disabled:opacity-40"
                  disabled={slides.length <= 1}
                  onClick={() => setSlideIdx((i) => (i - 1 + slides.length) % slides.length)}
                  aria-label="上一页"
                >
                  <ChevronLeft className="size-4" />
                </button>
                <button
                  type="button"
                  className="inline-flex size-8 items-center justify-center rounded-md border bg-background text-foreground transition-colors hover:bg-muted disabled:pointer-events-none disabled:opacity-40"
                  disabled={slides.length <= 1}
                  onClick={() => setSlideIdx((i) => (i + 1) % slides.length)}
                  aria-label="下一页"
                >
                  <ChevronRight className="size-4" />
                </button>
                <button
                  type="button"
                  className="ml-1 inline-flex h-8 items-center gap-1 rounded-md border bg-background px-2.5 text-xs font-medium transition-colors hover:bg-muted disabled:opacity-40"
                  disabled={slides.length <= 1}
                  onClick={() => setPlaying((p) => !p)}
                >
                  {playing ? <Pause className="size-3.5" /> : <Play className="size-3.5" />}
                  {playing ? "暂停" : "播放"}
                </button>
              </div>
              <span className="text-xs tabular-nums text-muted-foreground">
                {slideIdx + 1} / {slides.length}
              </span>
            </div>
          ) : null}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
