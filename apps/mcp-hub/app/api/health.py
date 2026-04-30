"""Health endpoints."""

from __future__ import annotations

from fastapi import APIRouter

from app import __version__

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok", "service": "mcp-hub", "version": __version__}


@router.get("/readyz")
async def readyz() -> dict[str, str]:
    # MCP Hub has no external deps required for readiness (M2).
    return {"status": "ready"}
