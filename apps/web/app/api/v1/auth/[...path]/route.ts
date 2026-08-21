import { NextRequest, NextResponse } from "next/server";

import { ACCESS_TOKEN_COOKIE } from "@/lib/auth/cookie";

export const dynamic = "force-dynamic";
export const revalidate = 0;

function coreOrigin(): string {
  return (process.env.AGENT_CORE_URL || process.env.NEXT_PUBLIC_API_BASE || "http://127.0.0.1:8000").replace(
    /\/$/,
    "",
  );
}

function cookieSecure(req: NextRequest): boolean {
  if (process.env.COOKIE_SECURE === "true") return true;
  if (process.env.COOKIE_SECURE === "false") return false;
  if (process.env.ENV === "prod") return true;
  const proto = req.headers.get("x-forwarded-proto")?.split(",")[0]?.trim();
  return proto === "https";
}

function cookieMaxAgeSec(): number {
  const hours = Number(process.env.JWT_EXPIRE_HOURS || 168);
  if (!Number.isFinite(hours) || hours <= 0) return 60 * 60 * 24 * 7;
  return Math.floor(hours * 3600);
}

function applyCookie(
  res: NextResponse,
  req: NextRequest,
  value: string,
  maxAge: number,
): void {
  res.cookies.set(ACCESS_TOKEN_COOKIE, value, {
    httpOnly: true,
    sameSite: "lax",
    path: "/",
    maxAge,
    secure: cookieSecure(req),
  });
}

async function proxy(req: NextRequest, path: string[]): Promise<NextResponse> {
  const joined = path.filter(Boolean).join("/");
  if (joined === "logout") {
    const res = NextResponse.json({ ok: true });
    applyCookie(res, req, "", 0);
    return res;
  }

  const url = `${coreOrigin()}/v1/auth/${joined}${req.nextUrl.search}`;
  const headers = new Headers();
  const cookie = req.headers.get("cookie");
  if (cookie) headers.set("cookie", cookie);
  const authorization = req.headers.get("authorization");
  if (authorization) headers.set("authorization", authorization);
  const contentType = req.headers.get("content-type");
  if (contentType) headers.set("content-type", contentType);

  const init: RequestInit = { method: req.method, headers, cache: "no-store" };
  if (req.method !== "GET" && req.method !== "HEAD") {
    init.body = await req.text();
  }

  const upstream = await fetch(url, init);
  const raw = await upstream.arrayBuffer();
  if (raw.byteLength === 0) {
    return new NextResponse(null, { status: upstream.status });
  }

  const upstreamCt = upstream.headers.get("content-type") || "";
  let payload: BodyInit = raw;
  let token: string | undefined;
  if (upstreamCt.includes("application/json")) {
    try {
      const json: unknown = JSON.parse(new TextDecoder().decode(raw));
      if (json && typeof json === "object" && "access_token" in json) {
        const rec = json as Record<string, unknown>;
        if (typeof rec.access_token === "string" && rec.access_token) {
          token = rec.access_token;
        }
        const { access_token: _omit, ...rest } = rec;
        payload = JSON.stringify(rest);
      }
    } catch {
      /* keep raw body */
    }
  }

  const res = new NextResponse(payload, { status: upstream.status });
  if (typeof payload === "string") {
    res.headers.set("content-type", "application/json");
  } else if (upstreamCt) {
    res.headers.set("content-type", upstreamCt);
  }
  if (token && upstream.ok) {
    applyCookie(res, req, token, cookieMaxAgeSec());
  }
  return res;
}

async function handler(req: NextRequest, context: { params: { path: string[] } }) {
  return proxy(req, context.params.path ?? []);
}

export const GET = handler;
export const POST = handler;
export const PUT = handler;
export const PATCH = handler;
export const DELETE = handler;
