"""Phase B: materialize Frame/Plan as sandbox JSON; append-only executor progress.

Events + LangGraph checkpoint remain authoritative for replay; these files are
debuggable snapshots and stable path pointers for tools / future memory index.

Layout (sandbox-relative):
  .somna/runs/<run_id>/task_frame.json
  .somna/runs/<run_id>/plan.json
  .somna/runs/<run_id>/progress.log
  .somna/runs/<run_id>/run_index.json   # minimal discovery manifest (P2)
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from app.logging_setup import get_logger
from app.tools.client import get_client

log = get_logger(__name__)

RUN_PREFIX = ".somna/runs"


def run_state_dir(run_id: str) -> str:
    safe = (run_id or "unknown").strip().replace("..", "").strip("/\\")
    return f"{RUN_PREFIX}/{safe}"


def task_frame_rel(run_id: str) -> str:
    return f"{run_state_dir(run_id)}/task_frame.json"


def plan_rel(run_id: str) -> str:
    return f"{run_state_dir(run_id)}/plan.json"


def progress_rel(run_id: str) -> str:
    return f"{run_state_dir(run_id)}/progress.log"


def run_index_rel(run_id: str) -> str:
    return f"{run_state_dir(run_id)}/run_index.json"


async def persist_run_index(state: dict[str, Any]) -> None:
    """Small manifest so observability tools can find frame/plan/progress without scanning."""
    run_id = state.get("run_id")
    if not run_id:
        return
    sid = str(state.get("sandbox_id") or state["session_id"])
    rel = run_index_rel(run_id)
    payload = {
        "kind": "somna.run_artifacts_index",
        "version": 1,
        "run_id": run_id,
        "session_id": str(state["session_id"]),
        "sandbox_id": sid,
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "artifacts": {
            "task_frame": task_frame_rel(run_id),
            "plan": plan_rel(run_id),
            "progress": progress_rel(run_id),
            "index": rel,
        },
    }
    await persist_json(
        sandbox_id=sid,
        session_id=state["session_id"],
        run_id=run_id,
        rel_path=rel,
        payload=payload,
    )


async def append_executor_progress_snapshot(
    state: dict[str, Any],
    *,
    sandbox_id: str,
    run_id: str | None,
    tool_turns: int,
    ok_calls: int,
    n_written: int,
    n_verified: int,
    note: str,
) -> None:
    if not run_id:
        return
    line = (
        f"tool_turn={tool_turns} ok_calls={ok_calls} "
        f"written={n_written} verified={n_verified} {note}"
    )
    await append_progress_line(
        sandbox_id=sandbox_id,
        session_id=state["session_id"],
        run_id=run_id,
        line=line,
    )


async def sync_plan_artifact(state: dict[str, Any], plan_obj: dict[str, Any] | None) -> None:
    """Rewrite plan.json when in-memory plan changes (todos / progress)."""
    if not plan_obj or not state.get("run_id"):
        return
    await persist_plan_pointer(state, plan_obj)


async def persist_json(
    *,
    sandbox_id: str,
    session_id: UUID,
    run_id: str,
    rel_path: str,
    payload: Any,
) -> bool:
    try:
        body = json.dumps(payload, ensure_ascii=False, indent=2)
        tool = await get_client().invoke(
            "filesystem",
            sandbox_id=sandbox_id,
            session_id=str(session_id),
            run_id=run_id,
            args={"action": "write", "path": rel_path, "content": body},
        )
        if not tool.ok:
            log.warning("run_artifacts.json_write_failed", path=rel_path, error=tool.error)
            return False
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("run_artifacts.json_write_exception", path=rel_path, error=str(exc))
        return False


async def append_progress_line(
    *,
    sandbox_id: str,
    session_id: UUID,
    run_id: str,
    line: str,
) -> None:
    rel = progress_rel(run_id)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    row = f"{ts}\t{line}\n"
    try:
        tool = await get_client().invoke(
            "filesystem",
            sandbox_id=sandbox_id,
            session_id=str(session_id),
            run_id=run_id,
            args={"action": "append", "path": rel, "content": row},
        )
        if not tool.ok:
            log.warning("run_artifacts.progress_append_failed", path=rel, error=tool.error)
    except Exception as exc:  # noqa: BLE001
        log.warning("run_artifacts.progress_append_exception", path=rel, error=str(exc))


async def persist_task_frame_pointer(
    state: dict[str, Any],
    frame: dict[str, Any],
) -> str | None:
    run_id = state.get("run_id")
    if not run_id:
        return None
    rel = task_frame_rel(run_id)
    sid = str(state.get("sandbox_id") or state["session_id"])
    await persist_json(
        sandbox_id=sid,
        session_id=state["session_id"],
        run_id=run_id,
        rel_path=rel,
        payload=frame,
    )
    await persist_run_index(state)
    return rel


async def persist_plan_pointer(
    state: dict[str, Any],
    plan_obj: dict[str, Any],
) -> str | None:
    run_id = state.get("run_id")
    if not run_id:
        return None
    rel = plan_rel(run_id)
    sid = str(state.get("sandbox_id") or state["session_id"])
    await persist_json(
        sandbox_id=sid,
        session_id=state["session_id"],
        run_id=run_id,
        rel_path=rel,
        payload=plan_obj,
    )
    await persist_run_index(state)
    return rel
