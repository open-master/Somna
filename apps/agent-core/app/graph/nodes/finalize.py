"""finalize node: mark session run as done and emit terminal status."""

from __future__ import annotations

from somna_events import ErrorEvent, SessionPhase, StatusEvent

from app.events.emitter import emit
from app.graph.nodes.plan import _emit_plan_update
from app.graph.state import SessionState
from app.logging_setup import get_logger
from app.memory import add_memory
from app.memory import is_enabled as memory_enabled
from app.services.billing import settle_billing_run
from app.services.session_phase import mark_session_run_closed

log = get_logger(__name__)


async def finalize_node(state: SessionState) -> SessionState:
    session_id = state["session_id"]
    run_id = state.get("run_id")
    err = state.get("error")
    frame = state.get("task_frame") or {}
    plan = state.get("plan") if isinstance(state.get("plan"), dict) else {}
    todos = plan.get("todos") if isinstance(plan.get("todos"), list) else []
    has_unfinished = any(
        not isinstance(todo, dict) or str(todo.get("status") or "") != "done" for todo in todos
    )
    if todos:
        # Re-emit the authoritative snapshot before the terminal status so a
        # transient progress-event failure cannot leave the UI on stale TODOs.
        await _emit_plan_update(
            session_id=session_id,
            run_id=run_id,
            plan=plan,
            todos=todos,
        )

    if err:
        billing_outcome = "failure"
    elif frame.get("needs_clarification"):
        billing_outcome = "clarification"
    elif has_unfinished:
        billing_outcome = "partial"
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
        last_phase = SessionPhase.error.value
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
            last_phase = SessionPhase.waiting_user.value
        elif has_unfinished:
            await emit(
                StatusEvent(
                    session_id=session_id,
                    run_id=run_id,
                    phase=SessionPhase.partial,
                    message="本轮已结束，但部分步骤未完成",
                )
            )
            last_phase = SessionPhase.partial.value
        else:
            await emit(
                StatusEvent(
                    session_id=session_id,
                    run_id=run_id,
                    phase=SessionPhase.done,
                    message="完成",
                )
            )
            last_phase = SessionPhase.done.value
        final_status = "active"  # session remains active; run is done

    try:
        await mark_session_run_closed(
            session_id,
            status=final_status,
            last_phase=last_phase,
        )
    except Exception:  # noqa: BLE001
        log.exception("graph.finalize.session_status_failed", session_id=str(session_id))

    # Opportunistically capture a takeaway into long-term memory so next
    # sessions can reference it. Gated by MEMORY_ENABLED — failure is silent.
    if not err and not frame.get("needs_clarification") and not has_unfinished and memory_enabled():
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
