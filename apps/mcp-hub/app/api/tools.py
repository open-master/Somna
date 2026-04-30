"""Tool listing & invocation endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.config import get_settings
from app.sandbox.manager import get_sandbox_manager
from app.tools import registry
from app.tools.base import ToolContext, ToolManifest, ToolResult

router = APIRouter(prefix="/v1/tools", tags=["tools"])


class InvokeRequest(BaseModel):
    sandbox_id: str
    args: dict[str, Any] = {}
    session_id: str | None = None
    run_id: str | None = None


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

    try:
        return await tool.invoke(ctx, payload.args)
    except Exception as e:  # defensive; tools should not raise
        return ToolResult(ok=False, error=f"tool crashed: {type(e).__name__}: {e}")
