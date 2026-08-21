"""Tool listing & invocation endpoints."""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.config import get_settings
from app.sandbox.manager import get_sandbox_manager
from app.tools import registry
from app.tools.base import ToolContext, ToolManifest, ToolResult

router = APIRouter(prefix="/v1/tools", tags=["tools"])


class InvokeRequest(BaseModel):
    sandbox_id: str
    args: dict[str, Any] = Field(default_factory=dict)
    session_id: str | None = None
    run_id: str | None = None
    idempotency_key: str | None = Field(default=None, max_length=500)


_idempotency_locks: dict[str, asyncio.Lock] = {}


def _cache_path(*, sandbox_id: str, idempotency_key: str) -> Path:
    settings = get_settings()
    digest = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
    return Path(settings.sandbox_root) / ".tool-idempotency" / sandbox_id / f"{digest}.json"


def _read_cached_result(path: Path) -> ToolResult | None:
    try:
        return ToolResult.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write_cached_result(path: Path, result: ToolResult) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    temporary.write_text(result.model_dump_json(), encoding="utf-8")
    temporary.replace(path)


@router.get("", response_model=list[ToolManifest])
async def list_tools() -> list[ToolManifest]:
    return [t.manifest() for t in registry.all_tools()]


@router.get("/{name}", response_model=ToolManifest)
async def get_tool(name: str) -> ToolManifest:
    tool = registry.get(name)
    if tool is None:
        raise HTTPException(status_code=404, detail=f"tool {name!r} not found")
    return tool.manifest()


@router.post("/{name}/invoke", response_model=ToolResult)
async def invoke_tool(name: str, payload: InvokeRequest) -> ToolResult:
    tool = registry.get(name)
    if tool is None:
        raise HTTPException(status_code=404, detail=f"tool {name!r} not found")

    sm = get_sandbox_manager(get_settings())
    rec = sm.get_or_create(payload.sandbox_id)
    ctx = ToolContext(
        sandbox_id=rec.id,
        workdir=rec.workdir,
        session_id=payload.session_id,
        run_id=payload.run_id,
    )

    async def run_once() -> ToolResult:
        try:
            return await tool.invoke(ctx, payload.args)
        except Exception as e:  # defensive; tools should not raise
            return ToolResult(ok=False, error=f"tool crashed: {type(e).__name__}: {e}")

    if not payload.idempotency_key:
        return await run_once()

    path = _cache_path(sandbox_id=rec.id, idempotency_key=payload.idempotency_key)
    lock_key = str(path)
    lock = _idempotency_locks.setdefault(lock_key, asyncio.Lock())
    async with lock:
        cached = await asyncio.to_thread(_read_cached_result, path)
        if cached is not None:
            return cached
        result = await run_once()
        if result.ok:
            await asyncio.to_thread(_write_cached_result, path, result)
        return result
