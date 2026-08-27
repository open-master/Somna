"""ingest node: normalise user input, ensure sandbox, emit status."""

from __future__ import annotations

from langchain_core.messages import HumanMessage
from somna_events import SessionPhase, StatusEvent

from app.events.emitter import emit
from app.graph.state import SessionState
from app.logging_setup import get_logger
from app.services.attachments import materialize_attachments_to_sandbox
from app.tools.client import get_client

log = get_logger(__name__)


async def ingest_node(state: SessionState) -> SessionState:
    session_id = state["session_id"]
    run_id = state.get("run_id")
    log.info("graph.ingest", session_id=str(session_id), run_id=run_id)

    sandbox_id = state.get("sandbox_id") or str(session_id)
    sandbox_error: str | None = None
    try:
        await get_client().ensure_sandbox(sandbox_id)
    except Exception as exc:  # noqa: BLE001
        log.warning("graph.ingest.sandbox_failed", error=str(exc), sandbox_id=sandbox_id)
        sandbox_error = f"沙箱环境初始化失败：{exc}"

    await emit(
        StatusEvent(
            session_id=session_id,
            run_id=run_id,
            phase=SessionPhase.planning,
            message="收到任务，正在准备",
        )
    )

    text = state.get("user_message") or ""
    if sandbox_error:
        return {"sandbox_id": sandbox_id, "error": sandbox_error}
    attachments = list(state.get("attachments") or [])
    extra_lines: list[str] = []
    if attachments:
        ok_rows, err_lines = await materialize_attachments_to_sandbox(
            state["session_id"],
            state.get("run_id"),
            attachments,
        )
        if ok_rows:
            lines = "\n".join(
                f"- {row['filename']} → `{row['sandbox_path']}` ({row.get('mime', '')})" for row in ok_rows
            )
            extra_lines.append("\n\n[附件已写入沙箱]\n" + lines)
        for err in err_lines:
            log.warning("graph.ingest.attachment_error", error=err)
            extra_lines.append("\n[附件警告] " + err)

    human_body = text + "".join(extra_lines)
    existing = list(state.get("messages") or [])
    # Temporal may retry the whole activity with the same run_id. A stable
    # message id lets LangGraph's add_messages reducer replace the retried
    # ingest entry instead of duplicating the user's turn in checkpoint state.
    message_id = f"user:{run_id}" if run_id else None
    existing.append(HumanMessage(content=human_body, id=message_id))
    frame = state.get("task_frame") if isinstance(state.get("task_frame"), dict) else {}
    resume_execute = bool(frame.get("awaiting_execute_decision"))
    resume_goal = str(frame.get("execute_resume_goal") or "").strip() if resume_execute else ""
    if resume_execute:
        log.info(
            "graph.ingest.resume_execute",
            session_id=str(session_id),
            run_id=run_id,
        )
    payload: dict = {
        "messages": existing,
        "sandbox_id": sandbox_id,
        "tool_turns": 0,
        "total_agent_turns": 0,
        "total_execution_tokens": 0,
        "compact_memory": state.get("compact_memory") if resume_execute else None,
        "execution_summary": state.get("execution_summary") if resume_execute else None,
        "plan": state.get("plan") if resume_execute else None,
        "plan_path": state.get("plan_path") if resume_execute else None,
        "resume_execute": resume_execute,
        "resume_goal": resume_goal or None,
    }
    return payload
