from __future__ import annotations

import asyncio
from uuid import UUID

import psycopg
from somna_events import ErrorEvent, InterruptAckEvent, SessionPhase, StatusEvent

from app.config import get_settings
from app.events.emitter import emit
from app.graph.runtime import get_registry
from app.graph.session_graph import close_graph, get_compiled_graph
from app.logging_setup import get_logger
from app.storage.postgres import get_pool

log = get_logger(__name__)


def cancel_status(reason: str) -> tuple[SessionPhase, str]:
    if reason == "user_stop":
        return SessionPhase.stopped, "已停止"
    return SessionPhase.interrupted, "已中断"


async def run_session_graph(
    session_id: UUID,
    run_id: str,
    text: str,
    planner_model: str | None,
    executor_model: str | None,
    executor_engine: str = "native",
    compact_model: str | None = None,
    coder_model: str | None = None,
    reasoner_model: str | None = None,
    longctx_model: str | None = None,
) -> None:
    settings = get_settings()
    current = asyncio.current_task()
    if current is not None:
        await get_registry().register(session_id, run_id, current)
        get_registry().attach_cleanup(session_id)
    try:
        for attempt in range(2):
            graph = await get_compiled_graph()
            payload = {
                "session_id": session_id,
                "run_id": run_id,
                "user_message": text,
                "planner_model": planner_model or settings.agent_default_planner,
                "executor_model": executor_model or settings.agent_default_executor,
                "compact_model": compact_model or settings.agent_compact_model,
                "coder_model": coder_model or settings.agent_default_coder,
                "reasoner_model": reasoner_model or settings.agent_default_reasoner,
                "longctx_model": longctx_model or settings.agent_default_longctx,
                "executor_engine": (executor_engine or "native").lower(),
                "skip_planner": planner_model is None,
                "sandbox_id": str(session_id),
                "messages": [],
                "tool_turns": 0,
                "plan": None,
                "compact_memory": None,
                "execution_summary": None,
                "reflection": None,
                "reflection_count": 0,
                "next_node": None,
                "finished": False,
                "error": None,
            }
            cfg = {"configurable": {"thread_id": str(session_id)}}
            try:
                await graph.ainvoke(payload, config=cfg)
                break
            except psycopg.OperationalError as exc:
                log.warning(
                    "graph.checkpointer.db_lost",
                    session_id=str(session_id),
                    run_id=run_id,
                    attempt=attempt,
                    error=str(exc),
                )
                await close_graph()
                if attempt == 1:
                    raise
    except asyncio.CancelledError:
        run = get_registry().get(session_id)
        reason = (run.reason if run and run.reason else None) or "interrupted"
        phase, message = cancel_status(reason)
        log.info("session.run.cancelled", session_id=str(session_id), run_id=run_id, reason=reason)
        await emit(InterruptAckEvent(session_id=session_id, run_id=run_id, reason=reason))
        await emit(
            StatusEvent(
                session_id=session_id,
                run_id=run_id,
                phase=phase,
                message=message,
            )
        )
        try:
            pool = get_pool()
            async with pool.acquire() as conn:
                await conn.execute(
                    "UPDATE sessions SET status = 'paused', workflow_id = NULL, run_id = NULL, updated_at = now() WHERE id = $1",
                    session_id,
                )
        except Exception:  # noqa: BLE001
            pass
        raise
    except Exception as exc:  # noqa: BLE001
        log.exception("session.run.failed", session_id=str(session_id), error=str(exc))
        await emit(
            ErrorEvent(
                session_id=session_id,
                run_id=run_id,
                code="agent.run.failed",
                message=str(exc),
                retryable=True,
            )
        )
        await emit(
            StatusEvent(
                session_id=session_id,
                run_id=run_id,
                phase=SessionPhase.error,
                message=f"运行失败: {exc}",
            )
        )
        # 图未走到 finalize 时 sessions 仍会停留在 running，导致后续 POST /messages 409
        try:
            pool = get_pool()
            async with pool.acquire() as conn:
                await conn.execute(
                    "UPDATE sessions SET status = 'error', workflow_id = NULL, run_id = NULL, updated_at = now() WHERE id = $1",
                    session_id,
                )
        except Exception:  # noqa: BLE001
            pass
