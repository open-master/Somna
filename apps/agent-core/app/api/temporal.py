from __future__ import annotations

import json
import re
import uuid
from datetime import datetime
from enum import Enum
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from google.protobuf.json_format import MessageToDict
from pydantic import BaseModel
from temporalio.service import RPCError

from app.api.deps import CurrentUser, get_current_user
from app.config import get_settings
from app.events.emitter import emit
from app.graph.runtime import get_registry
from app.services.attachments import normalize_attachment_refs
from app.storage.postgres import get_pool
from app.temporal.client import get_temporal_client
from app.temporal.models import SessionWorkflowInput
from app.temporal.workflows import SessionRunWorkflow
from somna_events import InterruptAckEvent, SessionPhase, StatusEvent

router = APIRouter(prefix="/v1/temporal", tags=["temporal"])


def _session_uuid_from_workflow_id(workflow_id: str) -> uuid.UUID | None:
    m = re.match(r"^session:([0-9a-fA-F-]{36}):run:", workflow_id or "")
    if not m:
        return None
    try:
        return uuid.UUID(m.group(1))
    except ValueError:
        return None


async def _assert_workflow_session_user(workflow_id: str, user: CurrentUser) -> None:
    sid = _session_uuid_from_workflow_id(workflow_id)
    if sid is None:
        raise HTTPException(status_code=404, detail="workflow not found")
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT user_id FROM sessions WHERE id = $1", sid)
    if row is None or row["user_id"] != user.id:
        raise HTTPException(status_code=404, detail="workflow not found")


class TemporalActionResp(BaseModel):
    ok: bool
    workflow_id: str
    run_id: str | None = None
    action: str
    session_id: str | None = None


class TemporalRetryResp(BaseModel):
    ok: bool
    action: str
    workflow_id: str
    run_id: str
    session_id: str


@router.get("/workflows")
async def list_temporal_workflows(
    limit: int = 100,
    query: str = "WorkflowType='SessionRunWorkflow'",
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    temporal = await get_temporal_client()
    workflows: list[dict[str, Any]] = []
    try:
        async for item in temporal.list_workflows(query=query, limit=limit):
            workflows.append(_serialize_workflow(item))
    except RPCError as exc:
        raise HTTPException(status_code=503, detail=f"temporal unavailable: {exc}") from exc

    pool = get_pool()
    async with pool.acquire() as conn:
        user_session_ids = {
            row["id"] for row in await conn.fetch("SELECT id FROM sessions WHERE user_id = $1", user.id)
        }

    filtered: list[dict[str, Any]] = []
    for item in workflows:
        wid = str(item.get("workflow_id") or "")
        sid = _session_uuid_from_workflow_id(wid)
        if sid is not None and sid in user_session_ids:
            filtered.append(item)

    await _attach_sessions(filtered)
    return {"query": query, "workflows": filtered}


@router.get("/workflows/{workflow_id}")
async def get_temporal_workflow(
    workflow_id: str,
    run_id: str | None = None,
    history_limit: int = 100,
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    await _assert_workflow_session_user(workflow_id, user)
    temporal = await get_temporal_client()
    handle = temporal.get_workflow_handle(workflow_id, run_id=run_id)
    try:
        description = await handle.describe()
        history: list[dict[str, Any]] = []
        async for event in handle.fetch_history_events(page_size=min(max(history_limit, 20), 200)):
            history.append(_serialize_history_event(event))
            if len(history) >= history_limit:
                break
    except RPCError as exc:
        message = str(exc).lower()
        if "not found" in message:
            raise HTTPException(status_code=404, detail=f"workflow not found: {workflow_id}") from exc
        raise HTTPException(status_code=503, detail=f"temporal unavailable: {exc}") from exc

    workflow = _serialize_workflow(description)
    session = await _session_for_workflow_id(workflow_id)
    return {
        "workflow": workflow,
        "history": history,
        "session": session,
    }


@router.post("/workflows/{workflow_id}/cancel", response_model=TemporalActionResp)
async def cancel_temporal_workflow(
    workflow_id: str,
    run_id: str | None = None,
    reason: str = "user_interrupt",
    user: CurrentUser = Depends(get_current_user),
) -> TemporalActionResp:
    await _assert_workflow_session_user(workflow_id, user)
    temporal = await get_temporal_client()
    handle = temporal.get_workflow_handle(workflow_id, run_id=run_id)
    session_row = await _session_row_for_workflow_id(workflow_id)
    if session_row is not None:
        await get_registry().set_reason(session_row["id"], reason=reason)
    try:
        await handle.cancel()
    except RPCError as exc:
        message = str(exc).lower()
        if "not found" in message:
            raise HTTPException(status_code=404, detail=f"workflow not found: {workflow_id}") from exc
        raise HTTPException(status_code=503, detail=f"temporal unavailable: {exc}") from exc
    return TemporalActionResp(
        ok=True,
        workflow_id=workflow_id,
        run_id=run_id,
        action="cancel",
        session_id=str(session_row["id"]) if session_row is not None else None,
    )


@router.post("/workflows/{workflow_id}/terminate", response_model=TemporalActionResp)
async def terminate_temporal_workflow(
    workflow_id: str,
    run_id: str | None = None,
    reason: str = "user_stop",
    user: CurrentUser = Depends(get_current_user),
) -> TemporalActionResp:
    await _assert_workflow_session_user(workflow_id, user)
    temporal = await get_temporal_client()
    handle = temporal.get_workflow_handle(workflow_id, run_id=run_id)
    session_row = await _session_row_for_workflow_id(workflow_id)
    try:
        await handle.terminate(reason=reason)
    except RPCError as exc:
        message = str(exc).lower()
        if "not found" in message:
            raise HTTPException(status_code=404, detail=f"workflow not found: {workflow_id}") from exc
        raise HTTPException(status_code=503, detail=f"temporal unavailable: {exc}") from exc

    if session_row is not None:
        await _mark_session_stopped(session_row["id"])
        await emit(InterruptAckEvent(session_id=session_row["id"], run_id=session_row["run_id"], reason=reason))
        await emit(
            StatusEvent(
                session_id=session_row["id"],
                run_id=session_row["run_id"],
                phase=SessionPhase.stopped,
                message="已强制终止 workflow",
            )
        )

    return TemporalActionResp(
        ok=True,
        workflow_id=workflow_id,
        run_id=run_id,
        action="terminate",
        session_id=str(session_row["id"]) if session_row is not None else None,
    )


@router.post("/workflows/{workflow_id}/retry", response_model=TemporalRetryResp)
async def retry_temporal_workflow(
    workflow_id: str,
    user: CurrentUser = Depends(get_current_user),
) -> TemporalRetryResp:
    await _assert_workflow_session_user(workflow_id, user)
    session_row = await _session_row_for_workflow_id(workflow_id)
    if session_row is None:
        raise HTTPException(status_code=404, detail="linked session not found for workflow")
    if session_row["status"] == "running":
        raise HTTPException(status_code=409, detail="session already has an in-flight run")

    latest_text_raw, latest_atts = await _latest_user_message_payload(session_row["id"])
    latest_text = (latest_text_raw or "").strip()
    if not latest_text and not latest_atts:
        raise HTTPException(status_code=409, detail="session has no retryable user task")

    settings = get_settings()
    sid_raw = session_row["id"]
    sid_uuid = sid_raw if isinstance(sid_raw, uuid.UUID) else uuid.UUID(str(sid_raw))
    norm_att = normalize_attachment_refs(sid_uuid, latest_atts, settings=settings)
    workflow_text = latest_text or "请根据下方附件与用户意图完成任务。"

    new_run_id = f"run_{uuid.uuid4().hex[:12]}"
    new_workflow_id = f"session:{session_row['id']}:run:{new_run_id}"
    temporal = await get_temporal_client()
    try:
        await temporal.start_workflow(
            SessionRunWorkflow.run,
            SessionWorkflowInput(
                session_id=str(session_row["id"]),
                run_id=new_run_id,
                user_id=str(session_row["user_id"]),
                text=workflow_text,
                planner_model=session_row["planner_model"] or settings.agent_default_planner,
                executor_model=session_row["executor_model"] or settings.agent_default_executor,
                executor_engine="native",
                task_frame_model=session_row["task_frame_model"] or settings.agent_default_taskframe,
                mcp_tool_models=None,
                attachments=norm_att,
            ),
            id=new_workflow_id,
            task_queue=settings.temporal_task_queue,
        )
    except RPCError as exc:
        raise HTTPException(status_code=503, detail=f"temporal unavailable: {exc}") from exc

    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE sessions
            SET status = 'running', workflow_id = $1, run_id = $2, updated_at = now()
            WHERE id = $3
            """,
            new_workflow_id,
            new_run_id,
            session_row["id"],
        )

    return TemporalRetryResp(
        ok=True,
        action="retry",
        workflow_id=new_workflow_id,
        run_id=new_run_id,
        session_id=str(session_row["id"]),
    )


async def _attach_sessions(workflows: list[dict[str, Any]]) -> None:
    workflow_ids = [item["workflow_id"] for item in workflows if item.get("workflow_id")]
    if not workflow_ids:
        return

    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, title, status, workflow_id, run_id, created_at, updated_at
            FROM sessions
            WHERE workflow_id = ANY($1::text[])
            """,
            workflow_ids,
        )
    by_workflow_id = {str(row["workflow_id"]): _serialize_session_row(row) for row in rows}
    for item in workflows:
        item["session"] = by_workflow_id.get(item.get("workflow_id"))


async def _session_for_workflow_id(workflow_id: str) -> dict[str, Any] | None:
    row = await _session_row_for_workflow_id(workflow_id)
    return _serialize_session_row(row) if row else None


async def _session_row_for_workflow_id(workflow_id: str) -> Any:
    pool = get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            """
            SELECT id, title, status, workflow_id, run_id, planner_model, task_frame_model, executor_model, created_at, updated_at
            FROM sessions
            WHERE workflow_id = $1
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            workflow_id,
        )


async def _latest_user_message_payload(session_id: Any) -> tuple[str | None, list[dict[str, Any]]]:
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT content
            FROM messages
            WHERE session_id = $1 AND role = 'user'
            ORDER BY created_at DESC
            LIMIT 1
            """,
            session_id,
        )
    if row is None or row["content"] is None:
        return None, []
    content = row["content"]
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except json.JSONDecodeError:
            return content, []
    if isinstance(content, dict):
        text = content.get("text")
        text_out = text if isinstance(text, str) else None
        raw_atts = content.get("attachments")
        atts: list[dict[str, Any]] = []
        if isinstance(raw_atts, list):
            atts = [a for a in raw_atts if isinstance(a, dict)]
        return text_out, atts
    return None, []


async def _mark_session_stopped(session_id: Any) -> None:
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE sessions
            SET status = 'stopped', workflow_id = NULL, run_id = NULL, updated_at = now()
            WHERE id = $1
            """,
            session_id,
        )


def _serialize_session_row(row: Any) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "title": row["title"],
        "status": row["status"],
        "workflow_id": row["workflow_id"],
        "run_id": row["run_id"],
        "created_at": _to_iso(row["created_at"]),
        "updated_at": _to_iso(row["updated_at"]),
    }


def _serialize_workflow(item: Any) -> dict[str, Any]:
    return {
        "workflow_id": _value(item, "id", "workflow_id"),
        "run_id": _value(item, "run_id"),
        "status": _enum_name(_value(item, "status")),
        "workflow_type": _name_like(_value(item, "workflow_type")),
        "task_queue": _name_like(_value(item, "task_queue")),
        "start_time": _to_iso(_value(item, "start_time")),
        "close_time": _to_iso(_value(item, "close_time")),
        "execution_time": _to_iso(_value(item, "execution_time")),
        "history_length": _value(item, "history_length"),
        "namespace": _value(item, "namespace"),
        "parent_id": _value(item, "parent_id"),
        "parent_run_id": _value(item, "parent_run_id"),
        "root_id": _value(item, "root_id"),
        "root_run_id": _value(item, "root_run_id"),
        "search_attributes": _json_safe(_value(item, "search_attributes")),
    }


def _serialize_history_event(event: Any) -> dict[str, Any]:
    raw = MessageToDict(event, preserving_proto_field_name=True)
    diagnosis = _extract_event_diagnosis(raw)
    return {
        "event_id": _value(event, "event_id"),
        "event_type": _enum_name(_value(event, "event_type")),
        "event_time": _to_iso(_value(event, "event_time")),
        "summary": diagnosis["summary"],
        "reason": diagnosis["reason"],
        "retry_state": diagnosis["retry_state"],
        "new_execution_run_id": diagnosis["new_execution_run_id"],
        "failure_message": diagnosis["failure_message"],
        "failure_chain": diagnosis["failure_chain"],
        "details": raw,
    }


def _value(obj: Any, *names: str) -> Any:
    for name in names:
        if hasattr(obj, name):
            return getattr(obj, name)
    return None


def _enum_name(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, Enum):
        return value.name
    name = getattr(value, "name", None)
    if isinstance(name, str):
        return name
    return str(value)


def _name_like(value: Any) -> Any:
    if value is None:
        return None
    name = getattr(value, "name", None)
    if isinstance(name, str):
        return name
    return value


def _to_iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    iso = getattr(value, "isoformat", None)
    if callable(iso):
        return iso()
    return str(value)


def _json_safe(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    if isinstance(value, (list, str, int, float, bool)):
        return value
    raw = getattr(value, "__dict__", None)
    if isinstance(raw, dict):
        return raw
    return str(value)


def _extract_event_diagnosis(raw: dict[str, Any]) -> dict[str, Any]:
    attrs = next((value for key, value in raw.items() if key.endswith("_event_attributes") and isinstance(value, dict)), {})
    failure = _first_failure(attrs)
    failure_chain = _failure_chain(failure)
    failure_message = failure_chain[0] if failure_chain else None
    reason = _first_string(attrs, ("reason", "cause", "cancel_reason", "terminate_reason"))
    retry_state = _first_string(attrs, ("retry_state",))
    new_execution_run_id = _first_string(attrs, ("new_execution_run_id",))
    summary = (
        reason
        or failure_message
        or retry_state
        or _first_string(attrs, ("workflow_type", "activity_type", "timer_id"))
        or None
    )
    return {
        "summary": summary,
        "reason": reason,
        "retry_state": retry_state,
        "new_execution_run_id": new_execution_run_id,
        "failure_message": failure_message,
        "failure_chain": failure_chain,
    }


def _first_failure(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        failure = value.get("failure")
        if isinstance(failure, dict):
            return failure
        for nested in value.values():
            candidate = _first_failure(nested)
            if candidate:
                return candidate
    elif isinstance(value, list):
        for nested in value:
            candidate = _first_failure(nested)
            if candidate:
                return candidate
    return None


def _failure_chain(failure: dict[str, Any] | None) -> list[str]:
    chain: list[str] = []
    current = failure
    while isinstance(current, dict):
        msg = current.get("message")
        if isinstance(msg, str) and msg.strip():
            chain.append(msg.strip())
        cause = current.get("cause")
        current = cause if isinstance(cause, dict) else None
    return chain


def _first_string(value: Any, keys: tuple[str, ...]) -> str | None:
    if isinstance(value, dict):
        for key in keys:
            item = value.get(key)
            if isinstance(item, str) and item.strip():
                return item.strip()
        for nested in value.values():
            result = _first_string(nested, keys)
            if result:
                return result
    elif isinstance(value, list):
        for nested in value:
            result = _first_string(nested, keys)
            if result:
                return result
    return None
