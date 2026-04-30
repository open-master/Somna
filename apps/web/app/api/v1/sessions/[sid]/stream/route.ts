import type { NextRequest } from "next/server";

export const dynamic = "force-dynamic";
export const revalidate = 0;

/**
 * Proxy session SSE to agent-core with proper streaming headers.
 * Next.js `rewrites()` can buffer or mishandle long-lived EventSource responses;
 * an explicit Route Handler keeps the body as a passthrough stream.
 */
export async function GET(req: NextRequest, context: { params: { sid: string } }) {
  const sid = context.params.sid;
  const since = req.nextUrl.searchParams.get("since") ?? "0";
  const origin = (process.env.AGENT_CORE_URL ?? "http://127.0.0.1:8000").replace(/\/$/, "");
  const url = `${origin}/v1/sessions/${encodeURIComponent(sid)}/stream?since=${encodeURIComponent(since)}`;

  const upstream = await fetch(url, {
    cache: "no-store",
    headers: { Accept: "text/event-stream" },
  });

  if (!upstream.ok) {
    return new Response(await upstream.text(), {
      status: upstream.status,
      statusText: upstream.statusText,
    });
  }

  if (!upstream.body) {
    return new Response("upstream empty body", { status: 502 });
  }

  const ct = upstream.headers.get("content-type") ?? "text/event-stream; charset=utf-8";

  return new Response(upstream.body, {
    status: upstream.status,
    headers: {
      "Content-Type": ct,
      "Cache-Control": "no-cache, no-transform",
      Connection: "keep-alive",
      "X-Accel-Buffering": "no",
    },
  });
}
