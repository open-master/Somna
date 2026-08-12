"""finalize node: mark session run as done and emit terminal status."""

from __future__ import annotations

from somna_events import ErrorEvent, SessionPhase, StatusEvent

from app.events.emitter import emit
from app.graph.state import SessionState
from app.logging_setup import get_logger
from app.memory import add_memory
from app.memory import is_enabled as memory_enabled
from app.services.billing import settle_billing_run
from app.storage.postgres import get_pool

log = get_logger(__name__)


async def finalize_node(state: SessionState) -> SessionState:
    session_id = state["session_id"]
    run_id = state.get("run_id")
    err = state.get("error")
    frame = state.get("task_frame") or {}

    if err:
        billing_outcome = "failure"
    elif frame.get("needs_clarification"):
        billing_outcome = "clarification"
    else:
        billing_outcome = "success"
    if state.get("user_id"):
        try:
            await settle_billing_run(run_id=run_id, outcome=billing_outcome)
        except Exception as exc:  # noqa: BLE001
            log.exception("graph.finalize.billing_failed", run_id=run_id, error=str(exc))

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
        if frame.get("needs_clarification"):
            # 本轮已输出追问，会话仍在等待用户补充，不应标记为「整个任务已完成」。
            await emit(
                StatusEvent(
                    session_id=session_id,
                    run_id=run_id,
                    phase=SessionPhase.waiting_user,
                    message="等待您补充信息后再继续",
                )
            )
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
