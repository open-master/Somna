"""Resume a session graph from the latest LangGraph checkpoint.

Temporal retries the whole Activity after worker crash / heartbeat timeout.
`run_session_graph` used to `ainvoke` a fresh START payload every attempt, which
re-entered ingest and zeroed plan / budgets. Same `thread_id` + `ainvoke(None)`
continues from the next node instead.
"""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from app.logging_setup import get_logger

log = get_logger(__name__)

ResumeAction = Literal["start", "resume", "skip"]


def graph_thread_config(session_id: UUID | str) -> dict[str, dict[str, str]]:
    return {"configurable": {"thread_id": str(session_id)}}


def checkpoint_resume_action(snapshot: Any, run_id: str) -> ResumeAction:
    """Decide whether this Activity attempt should start, resume, or no-op.

    - start: no checkpoint, or checkpoint belongs to a different user run.
    - resume: same run_id and the graph still has a next node (crash mid-graph).
    - skip: same run_id already reached END (Activity died after the graph finished).
    """
    values = getattr(snapshot, "values", None) or {}
    if not isinstance(values, dict) or not values:
        return "start"
    if str(values.get("run_id") or "") != str(run_id or ""):
        return "start"
    nxt = tuple(getattr(snapshot, "next", None) or ())
    if nxt:
        return "resume"
    return "skip"


async def invoke_session_graph(
    *,
    graph: Any,
    payload: dict[str, Any],
    session_id: UUID | str,
    run_id: str,
) -> ResumeAction:
    cfg = graph_thread_config(session_id)
    snapshot = await graph.aget_state(cfg)
    action = checkpoint_resume_action(snapshot, run_id)
    nxt = list(getattr(snapshot, "next", None) or ())
    if action == "skip":
        log.info(
            "session.run.checkpoint_skip",
            session_id=str(session_id),
            run_id=run_id,
            next=nxt,
        )
        return action
    if action == "resume":
        log.info(
            "session.run.checkpoint_resume",
            session_id=str(session_id),
            run_id=run_id,
            next=nxt,
        )
        await graph.ainvoke(None, config=cfg)
        return action
    log.info(
        "session.run.checkpoint_start",
        session_id=str(session_id),
        run_id=run_id,
        next=nxt,
    )
    await graph.ainvoke(payload, config=cfg)
    return action
