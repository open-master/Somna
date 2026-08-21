/** Cookie name for JWT — middleware、SSE 反代与登录 Set-Cookie 共用。JS 不可读（HttpOnly）。 */
export const ACCESS_TOKEN_COOKIE = "somna_access_token";

/** 浏览器请求走同源 Cookie，不再把 JWT 放进 Authorization。 */
export function authHeaders(): HeadersInit {
  return {};
}

/** 清除旧的非 HttpOnly Cookie（迁移用）；HttpOnly 必须走 /api/v1/auth/logout。 */
function clearLegacyJsCookie(): void {
  if (typeof document === "undefined") return;
  document.cookie = `${ACCESS_TOKEN_COOKIE}=; path=/; max-age=0`;
}

export async function logoutSession(): Promise<void> {
  try {
    await fetch("/api/v1/auth/logout", { method: "POST", credentials: "same-origin" });
  } catch {
    /* ignore network errors; still drop any leftover JS cookie */
  }
  clearLegacyJsCookie();
}
