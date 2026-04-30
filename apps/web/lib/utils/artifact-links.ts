/** Build artifact fetch URLs served by Next `/api/v1 → agent-core`. */

function parsed(url: string): URL {
  if (typeof window !== "undefined" && typeof window.location?.origin === "string") {
    return new URL(url, window.location.origin);
  }
  return new URL(url, "http://localhost");
}

/** 新标签页打开：inline，避免 Content-Disposition: attachment 只触发下载 */
export function artifactPreviewUrl(url: string): string {
  const u = parsed(url);
  u.searchParams.delete("download");
  u.searchParams.set("inline", "1");
  return `${u.pathname}${u.search}`;
}

/** 强制下载 */
export function artifactDownloadUrl(url: string): string {
  const u = parsed(url);
  u.searchParams.set("download", "1");
  u.searchParams.delete("inline");
  return `${u.pathname}${u.search}`;
}

/** 从 `/artifacts/content?path=` 解析沙盒相对路径（用于去重与合并）。 */
export function parseArtifactPathFromUrl(url: string): string | null {
  try {
    const p = parsed(url).searchParams.get("path");
    return p ? decodeURIComponent(p) : null;
  } catch {
    return null;
  }
}
