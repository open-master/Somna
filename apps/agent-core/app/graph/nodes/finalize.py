"""finalize node: mark session run as done and emit terminal status."""

from __future__ import annotations

from somna_events import ErrorEvent, SessionPhase, StatusEvent

from app.events.emitter import emit
from app.graph.state import SessionState
from app.logging_setup import get_logger
from app.memory import add_memory, is_enabled as memory_enabled
from app.storage.postgres import get_pool

log = get_logger(__name__)


async def finalize_node(state: SessionState) -> SessionState:
    session_id = state["session_id"]
    run_id = state.get("run_id")
    err = state.get("error")

    if err:
        await emit(
            ErrorEvent(
                session_id=session_id,
                run_id=run_id,
                code="agent.execute.failed",
                message=err,
                retryable=True,
            )
        )
        await emit(
            StatusEvent(
                session_id=session_id,
                run_id=run_id,
                phase=SessionPhase.error,
                message="运行失败",
            )
        )
        final_status = "error"
    else:
        await emit(
            StatusEvent(
                session_id=session_id,
                run_id=run_id,
                phase=SessionPhase.done,
                message="完成",
            )
        )
        final_status = "active"  # session remains active; run is done

    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE sessions SET status = $1, workflow_id = NULL, run_id = NULL, updated_at = now() WHERE id = $2",
            final_status,
            session_id,
        )

    # Opportunistically capture a takeaway into long-term memory so next
    # sessions can reference it. Gated by MEMORY_ENABLED — failure is silent.
    if not err and memory_enabled():
        user_msg = (state.get("user_message") or "").strip()
        answer = (state.get("assistant_text") or "").strip()
        if user_msg and answer:
            await add_memory(
                f"用户提问: {user_msg}\nAgent 回答: {answer[:800]}",
                session_id=str(session_id),
                user_id=state.get("user_id"),
                metadata={"kind": "exchange", "run_id": run_id},
            )

    log.info("graph.finalize", session_id=str(session_id), status=final_status)
    return {}
