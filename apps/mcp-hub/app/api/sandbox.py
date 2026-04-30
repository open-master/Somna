"""Sandbox lifecycle endpoints."""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.config import get_settings
from app.sandbox.manager import get_sandbox_manager

router = APIRouter(prefix="/v1/sandbox", tags=["sandbox"])


class CreateRequest(BaseModel):
    session_id: str | None = None


class SandboxInfo(BaseModel):
    id: str
    session_id: str | None = None
    workdir: str
    created_at: float
    last_used: float


@router.post("", response_model=SandboxInfo)
async def create(payload: CreateRequest) -> SandboxInfo:
    sm = get_sandbox_manager(get_settings())
    rec = sm.create(session_id=payload.session_id)
    return SandboxInfo(**{k: v for k, v in asdict(rec).items() if k in SandboxInfo.model_fields})


@router.get("", response_model=list[SandboxInfo])
async def list_sandboxes() -> list[SandboxInfo]:
    sm = get_sandbox_manager(get_settings())
    return [
        SandboxInfo(**{k: v for k, v in asdict(r).items() if k in SandboxInfo.model_fields})
        for r in sm.list_all()
    ]


@router.get("/{sandbox_id}", response_model=SandboxInfo)
async def get_sandbox(sandbox_id: str) -> SandboxInfo:
    sm = get_sandbox_manager(get_settings())
    rec = sm.get(sandbox_id)
    if rec is None:
        raise HTTPException(status_code=404, detail=f"sandbox {sandbox_id!r} not found")
    return SandboxInfo(**{k: v for k, v in asdict(rec).items() if k in SandboxInfo.model_fields})


@router.delete("/{sandbox_id}")
async def delete_sandbox(sandbox_id: str) -> dict[str, str]:
    sm = get_sandbox_manager(get_settings())
    ok = sm.delete(sandbox_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"sandbox {sandbox_id!r} not found")
    return {"status": "deleted", "id": sandbox_id}
