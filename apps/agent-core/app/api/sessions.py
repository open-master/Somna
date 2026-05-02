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
import re
import uuid
from pathlib import Path, PurePosixPath
from typing import Annotated, Any
from urllib.parse import quote as url_quote
from urllib.parse import unquote

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field
from temporalio.service import RPCError

from app.api.deps import CurrentUser, get_current_user
from app.config import get_settings
from app.events.emitter import fetch_history
from app.graph.runtime import get_registry
from app.logging_setup import get_logger
from app.storage.postgres import get_pool
from app.temporal.client import get_temporal_client
from app.temporal.models import SessionWorkflowInput
from app.temporal.workflows import SessionRunWorkflow
from app.services.session_cleanup import assert_session_owner, hard_delete_session
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


def _guess_media_type_for_artifact(path: str) -> str:
    """mimetypes 在部分环境对 Office 扩展映射不全；按扩展名补全。"""
    name = Path(path).name
    mt, _ = mimetypes.guess_type(name)
    if mt:
        return mt
    ext = Path(path).suffix.lower()
    mapping = {
        ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".ppt": "application/vnd.ms-powerpoint",
        ".doc": "application/msword",
        ".xls": "application/vnd.ms-excel",
    }
    return mapping.get(ext, "application/octet-stream")


# ----- Schemas -----
class CreateSessionReq(BaseModel):
    title: str = "New session"
    planner_model: str | None = None
    task_frame_model: str | None = None
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
    # 浏览器「MCP 工具」页：按工具名的默认 model（与 LiteLLM 角色分离）
    mcp_tool_models: dict[str, str] | None = None
    task_frame_model: str | None = None


def _strip_model(s: str | None) -> str | None:
    if s is None:
        return None
    t = s.strip()
    return t or None


def _mcp_overrides_from_post_message(req: PostMessageReq) -> dict[str, str] | None:
    out: dict[str, str] = {}
    if req.mcp_tool_models:
        for k, v in req.mcp_tool_models.items():
            ks = str(k).strip() if k is not None else ""
            if isinstance(v, str) and v.strip() and ks:
                out[ks] = v.strip()
    return out or None


class PostMessageResp(BaseModel):
    run_id: str
    queued: bool = True


class PatchSessionReq(BaseModel):
    title: str = Field(min_length=1, max_length=500)


# ----- CRUD -----
@router.get("")
async def list_sessions(
    limit: int = 100,
    user: CurrentUser = Depends(get_current_user),
) -> list[dict[str, Any]]:
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, user_id, title, status, planner_model, task_frame_model, executor_model, workflow_id, run_id, created_at, updated_at "
            "FROM sessions WHERE user_id = $1 ORDER BY updated_at DESC LIMIT $2",
            user.id,
            limit,
        )
    return [dict(row) for row in rows]


@router.post("", response_model=CreateSessionResp, status_code=201)
async def create_session(
    req: CreateSessionReq,
    user: CurrentUser = Depends(get_current_user),
) -> CreateSessionResp:
    s = get_settings()
    pool = get_pool()
    session_id = uuid.uuid4()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO sessions (id, user_id, title, planner_model, task_frame_model, executor_model, status)
            VALUES ($1, $2, $3, $4, $5, $6, 'active')
            """,
            session_id,
            user.id,
            req.title,
            None if req.skip_planner else (req.planner_model or s.agent_default_planner),
            req.task_frame_model or s.agent_default_taskframe,
            req.executor_model or s.agent_default_executor,
        )
    log.info("session.created", id=str(session_id), user_id=str(user.id))
    return CreateSessionResp(id=session_id, status="active", title=req.title)


@router.get("/{sid}")
async def get_session(sid: uuid.UUID, user: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    row = await assert_session_owner(sid, user.id)
    return {
        k: row[k]
        for k in (
            "id",
            "user_id",
            "title",
            "status",
            "planner_model",
            "task_frame_model",
            "executor_model",
            "workflow_id",
            "run_id",
            "created_at",
            "updated_at",
        )
        if k in row
    }


@router.patch("/{sid}")
async def patch_session(
    sid: uuid.UUID,
    req: PatchSessionReq,
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    await assert_session_owner(sid, user.id)
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE sessions SET title = $1, updated_at = now() WHERE id = $2 AND user_id = $3",
            req.title.strip(),
            sid,
            user.id,
        )
        row = await conn.fetchrow(
            "SELECT id, user_id, title, status, planner_model, task_frame_model, executor_model, workflow_id, run_id, created_at, updated_at "
            "FROM sessions WHERE id = $1",
            sid,
        )
    r = dict(row) if row else {}
    return {k: r[k] for k in ("id", "user_id", "title", "status", "planner_model", "task_frame_model", "executor_model", "workflow_id", "run_id", "created_at", "updated_at") if k in r}


@router.delete("/{sid}", status_code=204)
async def delete_session(
    sid: uuid.UUID,
    user: CurrentUser = Depends(get_current_user),
) -> Response:
    await hard_delete_session(session_id=sid, user_id=user.id)
    return Response(status_code=204)


# ----- Messages -----
@router.post("/{sid}/messages", response_model=PostMessageResp)
async def post_message(
    sid: uuid.UUID,
    req: PostMessageReq,
    user: CurrentUser = Depends(get_current_user),
) -> PostMessageResp:
    settings = get_settings()
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT planner_model, task_frame_model, executor_model, status FROM sessions WHERE id = $1 AND user_id = $2",
            sid,
            user.id,
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
    req_tf = _strip_model(req.task_frame_model)
    eff_pl = req_pl if req_pl is not None else row["planner_model"]
    eff_ex = req_ex if req_ex is not None else row["executor_model"]
    eff_tf = req_tf if req_tf is not None else row["task_frame_model"]
    eff_tf = eff_tf or settings.agent_default_taskframe

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
                task_frame_model=eff_tf,
                compact_model=_strip_model(req.compact_model),
                coder_model=_strip_model(req.coder_model),
                reasoner_model=_strip_model(req.reasoner_model),
                longctx_model=_strip_model(req.longctx_model),
                mcp_tool_models=_mcp_overrides_from_post_message(req),
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
                task_frame_model = $5,
                updated_at = now()
            WHERE id = $6
            """,
            workflow_id,
            run_id,
            eff_pl,
            store_ex,
            eff_tf,
            sid,
        )
    log.info("session.message.posted", session_id=str(sid), run_id=run_id)
    return PostMessageResp(run_id=run_id)


# ----- Interrupt -----
class InterruptResp(BaseModel):
    interrupted: bool
    run_id: str | None = None


@router.post("/{sid}/interrupt", response_model=InterruptResp)
async def interrupt_session(
    sid: uuid.UUID,
    reason: str = "user_interrupt",
    user: CurrentUser = Depends(get_current_user),
) -> InterruptResp:
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT workflow_id, run_id, status FROM sessions WHERE id = $1 AND user_id = $2",
            sid,
            user.id,
        )
    if row is None:
        raise HTTPException(status_code=404, detail="session not found")
    if row["status"] != "running" or not row["workflow_id"]:
        return InterruptResp(interrupted=False)
    await get_registry().set_reason(sid, reason=reason)
    temporal = await get_temporal_client()
    handle = temporal.get_workflow_handle(str(row["workflow_id"]))
    await handle.cancel()
    return InterruptResp(interrupted=True, run_id=row["run_id"])


def _artifact_path_candidates(raw: str) -> list[str]:
    """沙盒内相对路径候选：兼容仅 basename、缺 artifacts/ 前缀、以及宿主机 sandbox 绝对路径。"""
    s = unquote((raw or "").strip()).replace("\\", "/")
    if not s:
        return []
    while s.startswith("./"):
        s = s[2:]
    m = re.search(r"/sandboxes/[0-9a-fA-F-]{8,}/", s)
    if m:
        s = s[m.end() :].lstrip("./")
    s = s.lstrip("/")
    if not s or ".." in PurePosixPath(s).parts:
        return []

    out: list[str] = []
    seen: set[str] = set()

    def add(p: str) -> None:
        p = p.strip().lstrip("./")
        if not p or ".." in PurePosixPath(p).parts or p in seen:
            return
        seen.add(p)
        out.append(p)

    add(s)
    name = PurePosixPath(s).name
    if name:
        if name != s:
            add(name)
        add(f"artifacts/{name}")
    return out


@router.get("/{sid}/artifacts/content")
async def get_artifact_content(
    sid: uuid.UUID,
    path: str,
    download: Annotated[bool, Query()] = False,
    inline_preferred: Annotated[bool, Query(alias="inline")] = False,
    user: CurrentUser = Depends(get_current_user),
) -> Response:
    await assert_session_owner(sid, user.id)
    candidates = _artifact_path_candidates(path)
    if not candidates:
        raise HTTPException(400, "invalid path")

    await get_client().ensure_sandbox(str(sid))
    chosen: str | None = None
    body_b64: str | None = None
    for cand in candidates:
        tool = await get_client().invoke(
            "filesystem",
            sandbox_id=str(sid),
            args={"action": "read", "path": cand, "encoding": "base64"},
            session_id=str(sid),
        )
        if not tool.ok:
            continue
        out = tool.output if isinstance(tool.output, dict) else {}
        content = out.get("content")
        if not isinstance(content, str) or not content.strip():
            continue
        chosen = cand
        body_b64 = content.strip()
        break

    if not chosen or not body_b64:
        raise HTTPException(404, "artifact not found")

    try:
        raw = base64.b64decode(body_b64)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, f"invalid artifact payload: {exc}") from exc

    media_type = _guess_media_type_for_artifact(chosen)
    filename = Path(chosen).name
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
async def list_events(
    sid: uuid.UUID,
    since: int = 0,
    limit: int = 500,
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    await assert_session_owner(sid, user.id)
    events = await fetch_history(str(sid), since_seq=since, limit=limit)
    next_seq = events[-1]["seq"] if events else since
    return {"events": events, "next_seq": next_seq}
