/** 新建会话时的默认标题；与后端 createSession 默认一致 */
export const DEFAULT_SESSION_TITLE = "新会话";

export function isDefaultSessionTitle(title: string | undefined | null): boolean {
  const t = (title ?? "").trim();
  return t === "" || t === DEFAULT_SESSION_TITLE;
}

/** 用用户首条消息首行生成简短标题（不超过 maxLen 字符） */
export function titleFromUserMessage(text: string, maxLen = 48): string {
  const line = text.trim().split(/\r?\n/)[0] ?? "";
  const collapsed = line.replace(/\s+/g, " ").trim();
  if (!collapsed) return DEFAULT_SESSION_TITLE;
  if (collapsed.length <= maxLen) return collapsed;
  return `${collapsed.slice(0, Math.max(1, maxLen - 1))}…`;
}
