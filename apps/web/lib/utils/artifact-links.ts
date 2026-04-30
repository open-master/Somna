/** Build artifact fetch URLs served by Next `/api/v1 → agent-core`. */

function parsed(url: string): URL {
  const origin =
    typeof window !== "undefined" && typeof window.location?.origin === "string"
      ? window.location.origin
      : "http://localhost";
  let s = (url ?? "").trim();
  if (!s) return new URL("/", origin);
  // 无协议且不以 / 开头时，若用当前页为 base（如 /chat/xxx）会被拼到子路径下，导致 404、像「点不动」
  if (!/^https?:\/\//i.test(s) && !s.startsWith("/")) {
    s = `/${s}`;
  }
  return new URL(s, origin);
}

/**
 * 后端若设置 public_api_base，SSE 里可能是绝对地址 `http://agent:8000/v1/sessions/...`。
 * 只用 pathname 会变成站点上的 `/v1/...`，而 Next 只把 `/api/v1/*` 代理到 agent-core，导致预览/下载 404。
 */
function browserArtifactHref(u: URL): string {
  let path = u.pathname;
  if (path.startsWith("/v1/") && !path.startsWith("/api/")) {
    path = `/api${path}`;
  }
  return `${path}${u.search}`;
}

/** 新标签页打开：inline，避免 Content-Disposition: attachment 只触发下载 */
export function artifactPreviewUrl(url: string): string {
  const u = parsed(url);
  u.searchParams.delete("download");
  u.searchParams.set("inline", "1");
  return browserArtifactHref(u);
}

/** 强制下载 */
export function artifactDownloadUrl(url: string): string {
  const u = parsed(url);
  u.searchParams.set("download", "1");
  u.searchParams.delete("inline");
  return browserArtifactHref(u);
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
