"""Session + message REST endpoints.

This slice is intentionally minimal for the M2 smoke path:

    POST /v1/sessions                       — create a session
    GET  /v1/sessions/{id}                  — fetch a session
    POST /v1/sessions/{id}/messages         — send a user message (triggers a run)
    GET  /v1/sessions/{id}/events           — replay events (pagination)

The SSE stream lives in api/stream.py.
"""

from __future__ import annotations

import base64
import json
import mimetypes
import uuid
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import quote as url_quote

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field
from temporalio.service import RPCError

from app.config import get_settings
from app.events.emitter import fetch_history
from app.graph.runtime import get_registry
from app.logging_setup import get_logger
from app.storage.postgres import get_pool
from app.temporal.client import get_temporal_client
from app.temporal.models import SessionWorkflowInput
from app.temporal.workflows import SessionRunWorkflow
from app.tools.client import get_client

log = get_logger(__name__)
router = APIRouter(prefix="/v1/sessions", tags=["sessions"])


def _artifact_content_disposition(
    media_type: str,
    filename: str,
    *,
    force_download: bool,
    force_inline: bool,
) -> str:
    """inline：新标签可预览；attachment：下载。"""
    fq = url_quote(filename)
    if force_download:
        return f"attachment; filename*=UTF-8''{fq}"
    if force_inline:
        return f"inline; filename*=UTF-8''{fq}"
    if media_type.startswith(("text/", "image/")) or media_type in (
        "application/json",
        "application/javascript",
    ):
        return f"inline; filename*=UTF-8''{fq}"
    return f"attachment; filename*=UTF-8''{fq}"


# ----- Schemas -----
class CreateSessionReq(BaseModel):
    user_id: uuid.UUID | None = None
    title: str = "New session"
    planner_model: str | None = None
    executor_model: str | None = None
    skip_planner: bool = False


class CreateSessionResp(BaseModel):
    id: uuid.UUID
    status: str
    title: str


class PostMessageReq(BaseModel):
    text: str = Field(min_length=1)
    attachments: list[dict[str, Any]] = Field(default_factory=list)
    # native = OpenAI /v1；anthropic = LiteLLM /anthropic/v1（同一批 agent-* 别名 → 国产模型）
    executor_engine: str = "native"
    # Optional per-message overrides（前端「模型设置」；未传则用 sessions 表里的值）
    planner_model: str | None = None
    executor_model: str | None = None
    compact_model: str | None = None
    coder_model: str | None = None
    reasoner_model: str | None = None
    longctx_model: str | None = None


def _strip_model(s: str | None) -> str | None:
    if s is None:
        return None
    t = s.strip()
    return t or None


class PostMessageResp(BaseModel):
    run_id: str
    queued: bool = True


# ----- CRUD -----
@router.get("")
async def list_sessions(limit: int = 100) -> list[dict[str, Any]]:
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, user_id, title, status, planner_model, executor_model, workflow_id, run_id, created_at, updated_at "
            "FROM sessions ORDER BY updated_at DESC LIMIT $1",
            limit,
        )
    return [dict(row) for row in rows]


@router.post("", response_model=CreateSessionResp, status_code=201)
async def create_session(req: CreateSessionReq) -> CreateSessionResp:
    s = get_settings()
    pool = get_pool()
    session_id = uuid.uuid4()
    user_id = req.user_id or uuid.uuid4()  # dev shortcut; real auth comes in M3
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO sessions (id, user_id, title, planner_model, executor_model, status)
            VALUES ($1, $2, $3, $4, $5, 'active')
            """,
            session_id,
            user_id,
            req.title,
            None if req.skip_planner else (req.planner_model or s.agent_default_planner),
            req.executor_model or s.agent_default_executor,
        )
    log.info("session.created", id=str(session_id), user_id=str(user_id))
    return CreateSessionResp(id=session_id, status="active", title=req.title)


@router.get("/{sid}")
async def get_session(sid: uuid.UUID) -> dict[str, Any]:
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, user_id, title, status, planner_model, executor_model, workflow_id, run_id, created_at, updated_at "
            "FROM sessions WHERE id = $1",
            sid,
        )
    if row is None:
        raise HTTPException(404, "session not found")
    return dict(row)


# ----- Messages -----
@router.post("/{sid}/messages", response_model=PostMessageResp)
async def post_message(sid: uuid.UUID, req: PostMessageReq) -> PostMessageResp:
    settings = get_settings()
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT planner_model, executor_model, status FROM sessions WHERE id = $1",
            sid,
        )
        if row is None:
            raise HTTPException(404, "session not found")
        if row["status"] == "running":
            raise HTTPException(
                status_code=409,
                detail="session already has an in-flight run; interrupt first",
            )
        # persist user message
        await conn.execute(
            """INSERT INTO messages (session_id, role, content)
               VALUES ($1, 'user', $2::jsonb)""",
            sid,
            json.dumps({"text": req.text, "attachments": req.attachments}),
        )

    engine = (req.executor_engine or "native").strip().lower()
    if engine not in ("native", "anthropic", "anthropic_compat", "mode2"):
        engine = "native"
    if engine in ("anthropic_compat", "mode2"):
        engine = "anthropic"

    req_pl = _strip_model(req.planner_model)
    req_ex = _strip_model(req.executor_model)
    eff_pl = req_pl if req_pl is not None else row["planner_model"]
    eff_ex = req_ex if req_ex is not None else row["executor_model"]

    run_id = f"run_{uuid.uuid4().hex[:12]}"
    workflow_id = f"session:{sid}:run:{run_id}"
    temporal = await get_temporal_client()
    try:
        await temporal.start_workflow(
            SessionRunWorkflow.run,
            SessionWorkflowInput(
                session_id=str(sid),
                run_id=run_id,
                text=req.text,
                planner_model=eff_pl,
                executor_model=eff_ex,
                executor_engine=engine,
                compact_model=_strip_model(req.compact_model),
                coder_model=_strip_model(req.coder_model),
                reasoner_model=_strip_model(req.reasoner_model),
                longctx_model=_strip_model(req.longctx_model),
            ),
            id=workflow_id,
            task_queue=settings.temporal_task_queue,
        )
    except RPCError as exc:
        raise HTTPException(status_code=503, detail=f"temporal unavailable: {exc}") from exc

    async with pool.acquire() as conn:
        store_ex = eff_ex or settings.agent_default_executor
        await conn.execute(
            """
            UPDATE sessions
            SET status = 'running',
                workflow_id = $1,
                run_id = $2,
                planner_model = $3,
                executor_model = $4,
                updated_at = now()
            WHERE id = $5
            """,
            workflow_id,
            run_id,
            eff_pl,
            store_ex,
            sid,
        )
    log.info("session.message.posted", session_id=str(sid), run_id=run_id)
    return PostMessageResp(run_id=run_id)


# ----- Interrupt -----
class InterruptResp(BaseModel):
    interrupted: bool
    run_id: str | None = None


@router.post("/{sid}/interrupt", response_model=InterruptResp)
async def interrupt_session(sid: uuid.UUID, reason: str = "user_interrupt") -> InterruptResp:
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT workflow_id, run_id, status FROM sessions WHERE id = $1",
            sid,
        )
    if row is None or row["status"] != "running" or not row["workflow_id"]:
        return InterruptResp(interrupted=False)
    await get_registry().set_reason(sid, reason=reason)
    temporal = await get_temporal_client()
    handle = temporal.get_workflow_handle(str(row["workflow_id"]))
    await handle.cancel()
    return InterruptResp(interrupted=True, run_id=row["run_id"])


@router.get("/{sid}/artifacts/content")
async def get_artifact_content(
    sid: uuid.UUID,
    path: str,
    download: Annotated[bool, Query()] = False,
    inline_preferred: Annotated[bool, Query(alias="inline")] = False,
) -> Response:
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT id FROM sessions WHERE id = $1", sid)
    if row is None:
        raise HTTPException(404, "session not found")

    command = (
        "python - <<'PY'\n"
        "import base64\n"
        f"path = {path!r}\n"
        "with open(path, 'rb') as f:\n"
        "    print(base64.b64encode(f.read()).decode('ascii'))\n"
        "PY"
    )
    await get_client().ensure_sandbox(str(sid))
    tool = await get_client().invoke(
        "shell",
        sandbox_id=str(sid),
        args={"cmd": command},
        session_id=str(sid),
    )
    payload = (tool.output or {}).get("stdout") if tool.output else None
    if not tool.ok or not isinstance(payload, str) or not payload.strip():
        raise HTTPException(404, "artifact not found")

    try:
        raw = base64.b64decode(payload.strip())
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, f"invalid artifact payload: {exc}") from exc

    media_type = mimetypes.guess_type(path)[0] or "application/octet-stream"
    filename = Path(path).name
    disposition = _artifact_content_disposition(
        media_type,
        filename,
        force_download=download,
        force_inline=inline_preferred,
    )
    return Response(
        content=raw,
        media_type=media_type,
        headers={"Content-Disposition": disposition},
    )


# ----- Event replay -----
@router.get("/{sid}/events")
async def list_events(sid: uuid.UUID, since: int = 0, limit: int = 500) -> dict[str, Any]:
    events = await fetch_history(str(sid), since_seq=since, limit=limit)
    next_seq = events[-1]["seq"] if events else since
    return {"events": events, "next_seq": next_seq}
