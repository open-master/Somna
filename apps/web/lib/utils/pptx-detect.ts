/** 是否使用客户端 PPTX 简版预览（OOXML .pptx），而非新开标签页。 */
export function isPptxLike(fileName: string, mime: string): boolean {
  const name = (fileName || "").toLowerCase();
  if (name.endsWith(".pptx")) return true;
  const m = (mime || "").toLowerCase();
  if (m.includes("presentationml")) return true;
  if (m.includes("powerpoint") && m.includes("presentation")) return true;
  if (m === "application/octet-stream" && name.endsWith(".pptx")) return true;
  return false;
}
