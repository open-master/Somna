import JSZip from "jszip";

const NS_DRAW = "http://schemas.openxmlformats.org/drawingml/2006/main";

export type PptxSlideModel = {
  index: number;
  texts: string[];
  /** Blob object URLs — 调用方关闭预览时需 revoke */
  imageUrls: string[];
};

function normalizeZipRelativePath(slideXmlPath: string, target: string): string {
  const dir = slideXmlPath.replace(/[^/]+$/, "");
  const segments = [...dir.split("/").filter(Boolean), ...target.split("/")];
  const out: string[] = [];
  for (const s of segments) {
    if (s === "..") out.pop();
    else if (s !== "." && s) out.push(s);
  }
  return out.join("/");
}

function extToMime(ext: string): string | null {
  switch (ext.toLowerCase()) {
    case "jpg":
    case "jpeg":
      return "image/jpeg";
    case "gif":
      return "image/gif";
    case "webp":
      return "image/webp";
    case "png":
      return "image/png";
    case "svg":
      return "image/svg+xml";
    default:
      return null;
  }
}

export async function parsePptxToSlides(buf: ArrayBuffer): Promise<PptxSlideModel[]> {
  let zip: JSZip;
  try {
    zip = await JSZip.loadAsync(buf);
  } catch {
    throw new Error("无法解析文件（可能不是有效的 .pptx）");
  }

  const names = Object.keys(zip.files);
  const slideXmlPaths = names
    .filter((n) => {
      const o = zip.file(n);
      return Boolean(o && !o.dir && /^ppt\/slides\/slide\d+\.xml$/i.test(n));
    })
    .sort((a, b) => {
      const na = parseInt(/slide(\d+)\.xml$/i.exec(a)?.[1] ?? "0", 10);
      const nb = parseInt(/slide(\d+)\.xml$/i.exec(b)?.[1] ?? "0", 10);
      return na - nb;
    });

  if (slideXmlPaths.length === 0) {
    throw new Error("未找到幻灯片内容（可能为加密或旧版 .ppt）");
  }

  const slides: PptxSlideModel[] = [];

  for (const slidePath of slideXmlPaths) {
    const file = zip.file(slidePath);
    if (!file || file.dir) continue;
    const xml = await file.async("string");
    const doc = new DOMParser().parseFromString(xml, "application/xml");
    if (doc.getElementsByTagName("parsererror").length > 0) {
      slides.push({ index: slides.length + 1, texts: [], imageUrls: [] });
      continue;
    }

    const texts: string[] = [];
    const paragraphs = doc.getElementsByTagNameNS(NS_DRAW, "p");
    for (let i = 0; i < paragraphs.length; i++) {
      const p = paragraphs.item(i);
      if (!p) continue;
      const runs = p.getElementsByTagNameNS(NS_DRAW, "t");
      let line = "";
      for (let j = 0; j < runs.length; j++) {
        const tcell = runs.item(j);
        line += tcell?.textContent ?? "";
      }
      const t = line.trim();
      if (t) texts.push(t);
    }

    const imageUrls: string[] = [];
    const sn = /slide(\d+)\.xml$/i.exec(slidePath)?.[1];
    if (sn) {
      const relPath = `ppt/slides/_rels/slide${sn}.xml.rels`;
      const relFile = zip.file(relPath);
      if (relFile) {
        const relXml = await relFile.async("string");
        const relDoc = new DOMParser().parseFromString(relXml, "application/xml");
        const rels = relDoc.getElementsByTagName("Relationship");
        for (let i = 0; i < rels.length; i++) {
          const r = rels.item(i);
          if (!r) continue;
          const type = (r.getAttribute("Type") ?? "").toLowerCase();
          if (!type.includes("image")) continue;
          const target = r.getAttribute("Target") ?? "";
          if (!target) continue;
          const resolved = normalizeZipRelativePath(slidePath, target);
          const img = zip.file(resolved);
          if (!img) continue;
          const ext = resolved.split(".").pop() ?? "";
          if (/^(emf|wmf)$/i.test(ext)) continue;
          const imageMime = extToMime(ext);
          if (!imageMime) continue;
          const bytes = await img.async("uint8array");
          const copy = new Uint8Array(bytes.byteLength);
          copy.set(bytes);
          const blob = new Blob([copy], { type: imageMime });
          imageUrls.push(URL.createObjectURL(blob));
        }
      }
    }

    slides.push({ index: slides.length + 1, texts, imageUrls });
  }

  return slides;
}

export function revokePptxSlideUrls(slides: PptxSlideModel[]) {
  for (const s of slides) {
    for (const u of s.imageUrls) {
      try {
        URL.revokeObjectURL(u);
      } catch {
        /* noop */
      }
    }
  }
}
