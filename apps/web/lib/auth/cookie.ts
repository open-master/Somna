/** Cookie name for JWT — middleware 与 API 调用共用。 */
export const ACCESS_TOKEN_COOKIE = "somna_access_token";

export function getCookie(name: string): string | null {
  if (typeof document === "undefined") return null;
  const m = document.cookie.match(new RegExp(`(?:^|; )${name.replace(/[.$?*|{}()[\]\\/+^]/g, "\\$&")}=([^;]*)`));
  const g1 = m?.[1];
  return g1 != null ? decodeURIComponent(g1) : null;
}

export function setAccessTokenCookie(token: string, maxAgeSec = 60 * 60 * 24 * 7): void {
  if (typeof document === "undefined") return;
  const v = encodeURIComponent(token);
  document.cookie = `${ACCESS_TOKEN_COOKIE}=${v}; path=/; max-age=${maxAgeSec}; SameSite=Lax`;
}

export function clearAccessTokenCookie(): void {
  if (typeof document === "undefined") return;
  document.cookie = `${ACCESS_TOKEN_COOKIE}=; path=/; max-age=0`;
}

export function getAccessToken(): string | null {
  return getCookie(ACCESS_TOKEN_COOKIE);
}

export function authHeaders(): HeadersInit {
  const t = getAccessToken();
  return t ? { Authorization: `Bearer ${t}` } : {};
}
