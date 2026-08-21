export const dynamic = "force-dynamic";
export const revalidate = 0;

function agentCoreOrigin(): string {
  return (process.env.AGENT_CORE_URL || process.env.NEXT_PUBLIC_API_BASE || "http://127.0.0.1:8000").replace(
    /\/$/,
    "",
  );
}

export async function GET() {
  const origin = agentCoreOrigin();
  try {
    const upstream = await fetch(`${origin}/healthz`, {
      cache: "no-store",
      signal: AbortSignal.timeout(3000),
    });
    if (!upstream.ok) {
      return Response.json(
        { status: "unhealthy", agent_core: "error", http_status: upstream.status },
        { status: 503 },
      );
    }
    return Response.json({ status: "ok", agent_core: "ok" });
  } catch {
    return Response.json({ status: "unhealthy", agent_core: "unreachable" }, { status: 503 });
  }
}
