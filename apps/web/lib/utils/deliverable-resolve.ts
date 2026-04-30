/** Match markdown「生成的文件」表格里的文案到可访问的 artifacts/content URL。 */

const EXT =
  "mp4|webm|mov|mkv|png|jpe?g|gif|webp|md|txt|pdf|mp3|wav|m4a|zip|html?|pptx?";
const FILENAME_RE = new RegExp(
  String.raw`([\w./-][\w./\s-]*?\.(?:${EXT}))\b`,
  "gi",
);
const GLOB_FILENAME_RE = new RegExp(String.raw`([\w.*-]+\.(?:${EXT}))`, "i");

export interface ArtifactRef {
  name: string;
  url: string;
  description?: string;
}

export interface FileRef {
  path: string;
}

export function sessionArtifactContentUrl(sessionId: string, relativePath: string): string {
  const path = relativePath.replace(/^\.\//, "").trim();
  return `/api/v1/sessions/${sessionId}/artifacts/content?path=${encodeURIComponent(path)}`;
}

function stripCellNoise(s: string): string {
  return s
    .replace(/\s*\([^)]*\bMB\b[^)]*\)\s*/gi, " ")
    .replace(/\*\*/g, "")
    .replace(/`/g, "")
    .trim();
}

function globToRegExp(glob: string): RegExp {
  const escaped = glob
    .replace(/[.+^${}()|[\]\\]/g, "\\$&")
    .replace(/\*/g, ".*");
  return new RegExp(`^${escaped}$`, "i");
}

function candidatesFromDisplayText(displayText: string): string[] {
  const plain = stripCellNoise(displayText);
  const out: string[] = [];
  const seen = new Set<string>();
  const add = (raw: string) => {
    const c = raw.trim().replace(/^\.\//, "");
    if (c && !seen.has(c)) {
      seen.add(c);
      out.push(c);
    }
  };
  if (plain.includes("*")) {
    const gm = plain.match(GLOB_FILENAME_RE);
    if (gm?.[1]) add(gm[1]);
  }
  FILENAME_RE.lastIndex = 0;
  let m: RegExpExecArray | null;
  while ((m = FILENAME_RE.exec(plain)) !== null) {
    if (m[1]) add(m[1]);
  }
  return out;
}

function basenameOnly(p: string): string {
  const s = p.replace(/^\.\//, "").split("/").pop() ?? p;
  return s.trim();
}

function lookupOne(
  sessionId: string,
  token: string,
  artifacts: ArtifactRef[],
  fileItems: FileRef[],
): string | null {
  if (token.includes("*")) {
    const re = globToRegExp(basenameOnly(token));
    const art = artifacts.find((a) => re.test(a.name));
    if (art) return art.url;
    const fi = fileItems.find((f) => re.test(basenameOnly(f.path)));
    if (fi) return sessionArtifactContentUrl(sessionId, fi.path);
    return null;
  }
  const base = basenameOnly(token);
  const art = artifacts.find((a) => a.name === token || a.name === base);
  if (art) return art.url;
  const fi = fileItems.find(
    (f) => f.path === token || f.path.endsWith(`/${base}`) || basenameOnly(f.path) === base,
  );
  if (fi) return sessionArtifactContentUrl(sessionId, fi.path);
  return null;
}

/** 从表格单元格原文解析第一个可下载的 URL。 */
export function resolveDeliverableUrl(
  sessionId: string,
  firstCellPlainText: string,
  artifacts: ArtifactRef[],
  fileItems: FileRef[],
): string | null {
  for (const c of candidatesFromDisplayText(firstCellPlainText)) {
    const u = lookupOne(sessionId, c, artifacts, fileItems);
    if (u) return u;
  }
  return null;
}

export function tableHeaderLooksLikeFileManifest(headerRowPlainText: string): boolean {
  const t = headerRowPlainText.replace(/\s+/g, "");
  return /文件/.test(t) && (/说明|描述/.test(t) || /download/i.test(t));
}

/** 轨迹路径是否像可交付文件（与后端交付物扩展大致对齐）。 */
export function looksLikeDeliverableFilename(pathOrName: string): boolean {
  const base = pathOrName.replace(/^\.\//, "").split("/").pop() ?? pathOrName;
  return /\.(mp4|webm|mov|mkv|png|jpe?g|gif|webp|md|txt|pdf|mp3|wav|m4a|zip|html?|pptx?)$/i.test(
    base,
  );
}
