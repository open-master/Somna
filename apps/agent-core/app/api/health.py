"""Health endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Response

from app.storage.nats_client import is_connected as nats_is_connected
from app.storage.postgres import get_pool
from app.storage.redis_client import get_redis
from app.tools.client import get_client as get_mcp_client

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz() -> Response:
    """Liveness — must never touch deps."""
    return Response(content="ok", media_type="text/plain")


@router.get("/readyz")
async def readyz() -> dict:
    """Readiness — verifies downstream deps."""
    out = {"postgres": False, "redis": False, "nats": False, "mcp_hub": False}
    try:
        pool = get_pool()
        async with pool.acquire() as conn:
            await conn.execute("SELECT 1")
        out["postgres"] = True
    except Exception:  # noqa: BLE001
        pass
    try:
        r = get_redis()
        await r.ping()
        out["redis"] = True
    except Exception:  # noqa: BLE001
        pass
    try:
        out["nats"] = nats_is_connected()
    except Exception:  # noqa: BLE001
        pass
    try:
        client = get_mcp_client()
        r = await client._client.get("/healthz")
        out["mcp_hub"] = r.status_code == 200
    except Exception:  # noqa: BLE001
        pass
    out["ok"] = all(v for k, v in out.items() if k != "ok")
    return out
