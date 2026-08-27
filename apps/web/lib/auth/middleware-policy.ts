export type AuthGateDecision = "allow" | "api-unauthorized" | "redirect-login";

export function authGateDecision(pathname: string, hasToken: boolean): AuthGateDecision {
  if (
    pathname.startsWith("/login") ||
    pathname.startsWith("/register") ||
    pathname === "/" ||
    pathname === "/api/health" ||
    pathname.startsWith("/api/v1/auth") ||
    pathname.startsWith("/_next") ||
    pathname === "/favicon.ico"
  ) {
    return "allow";
  }
  if (hasToken) return "allow";
  if (pathname.startsWith("/api/")) return "api-unauthorized";
  return "redirect-login";
}
