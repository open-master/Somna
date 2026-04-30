"""ingest node: normalise user input, ensure sandbox, emit status."""

from __future__ import annotations

from langchain_core.messages import HumanMessage
from somna_events import SessionPhase, StatusEvent

from app.events.emitter import emit
from app.graph.state import SessionState
from app.logging_setup import get_logger
from app.tools.client import get_client

log = get_logger(__name__)


async def ingest_node(state: SessionState) -> SessionState:
    session_id = state["session_id"]
    run_id = state.get("run_id")
    log.info("graph.ingest", session_id=str(session_id), run_id=run_id)

    sandbox_id = state.get("sandbox_id") or str(session_id)
    try:
        await get_client().ensure_sandbox(sandbox_id)
    except Exception as exc:  # noqa: BLE001
        log.warning("graph.ingest.sandbox_failed", error=str(exc), sandbox_id=sandbox_id)

    await emit(
        StatusEvent(
            session_id=session_id,
            run_id=run_id,
            phase=SessionPhase.executing,
            message="收到任务，开始处理",
        )
    )

    text = state.get("user_message") or ""
    existing = list(state.get("messages") or [])
    existing.append(HumanMessage(content=text))
    return {"messages": existing, "sandbox_id": sandbox_id, "tool_turns": 0}
