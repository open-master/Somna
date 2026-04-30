/** 与 agent-core `_workspace_relative_from_guess` 对齐：去掉宿主机 `/.../sandboxes/<id>/` 前缀。 */

const SANDBOX_IN_PATH = /\/sandboxes\/[0-9a-fA-F-]{8,}\//;

export function normalizeWorkspacePath(path: string): string {
  const raw = path.trim().replace(/\\/g, "/");
  if (!raw || raw.includes("://")) return raw;
  const m = SANDBOX_IN_PATH.exec(raw);
  if (m && m.index !== undefined) {
    return raw.slice(m.index + m[0].length).replace(/^\.\/+/, "");
  }
  return raw.replace(/^\.\/+/, "");
}

/** 可用 session 的 artifacts/content 拉取的路径（沙盒工作区相对路径）。 */
export function isSessionArtifactRelativePath(path: string): boolean {
  const p = normalizeWorkspacePath(path);
  if (!p || p.includes("..")) return false;
  if (p.startsWith("/")) return false;
  return true;
}
