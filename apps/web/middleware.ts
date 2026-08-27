import type { NextRequest } from "next/server";
import { NextResponse } from "next/server";

import { authGateDecision } from "@/lib/auth/middleware-policy";

const TOKEN = "somna_access_token";

/** 未携带 JWT 时仅允许登录页、健康检查与 auth API（由 Next 反代到 agent-core）。 */
export function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;
  const token = request.cookies.get(TOKEN)?.value;
  const decision = authGateDecision(pathname, Boolean(token));
  if (decision === "allow") return NextResponse.next();
  if (decision === "api-unauthorized") {
    return NextResponse.json({ detail: "not authenticated" }, { status: 401 });
  }
  if (decision === "redirect-login") {
    const url = request.nextUrl.clone();
    url.pathname = "/login";
    url.searchParams.set("next", pathname);
    return NextResponse.redirect(url);
  }
  return NextResponse.next();
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
