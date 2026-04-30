"""Task framing — execution mode and routing before planner (phase A)."""

from __future__ import annotations

import json
import re
from typing import Any

from somna_events import SessionPhase, StatusEvent

from app.config import get_settings
from app.events.emitter import emit
from app.graph.state import SessionState
from app.llm.client import get_async_openai
from app.logging_setup import get_logger
from app.prompts.loader import load_template, render

log = get_logger(__name__)

_JSON_BLOCK_RE = re.compile(r"\{[\s\S]*\}", re.MULTILINE)

DEFAULT_TASK_FRAME: dict[str, Any] = {
    "needs_clarification": False,
    "clarification_questions": [],
    "task_mode": "full_pipeline",
    "effort_level": "medium",
    "risk_level": "low",
    "deliverable_type": "unspecified",
    "should_invoke_planner": True,
    "allowed_action_scope": [],
    "success_criteria": [],
    "reasoning_summary": "",
}

_BLANK_USER_REASON = "empty_user_message_short_circuit"


def frame_for_blank_user_message() -> dict[str, Any]:
    """Deterministic framing when there is no text intent (no LLM)."""
    out = dict(DEFAULT_TASK_FRAME)
    out["needs_clarification"] = True
    out["clarification_questions"] = ["请用一句话描述你想完成的任务或问题。"]
    out["should_invoke_planner"] = False
    out["task_mode"] = "direct_answer"
    out["deliverable_type"] = "unspecified"
    out["effort_level"] = "low"
    out["reasoning_summary"] = _BLANK_USER_REASON
    return out


def _coerce_str_list(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        s = raw.strip()
        return [s] if s else []
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    return []


def _parse_frame_json(raw: str) -> dict[str, Any] | None:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    m = _JSON_BLOCK_RE.search(raw)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def normalize_task_frame(parsed: dict[str, Any] | None) -> dict[str, Any]:
    out = dict(DEFAULT_TASK_FRAME)
    if not parsed or not isinstance(parsed, dict):
        out["reasoning_summary"] = "framing_unparseable_or_empty"
        return out

    out["needs_clarification"] = bool(parsed.get("needs_clarification"))
    out["clarification_questions"] = _coerce_str_list(parsed.get("clarification_questions"))
    out["task_mode"] = str(parsed.get("task_mode") or out["task_mode"]).strip() or out["task_mode"]
    out["effort_level"] = str(parsed.get("effort_level") or out["effort_level"]).strip() or out["effort_level"]
    out["risk_level"] = str(parsed.get("risk_level") or out["risk_level"]).strip() or out["risk_level"]
    out["deliverable_type"] = (
        str(parsed.get("deliverable_type") or out["deliverable_type"]).strip() or out["deliverable_type"]
    )
    if "should_invoke_planner" in parsed:
        out["should_invoke_planner"] = bool(parsed.get("should_invoke_planner"))
    out["allowed_action_scope"] = _coerce_str_list(parsed.get("allowed_action_scope"))
    out["success_criteria"] = _coerce_str_list(parsed.get("success_criteria"))
    out["reasoning_summary"] = str(parsed.get("reasoning_summary") or "").strip() or out["reasoning_summary"]

    # 与 session_graph 一致：本回合若需澄清，则不会进入 planner。
    if out["needs_clarification"]:
        out["should_invoke_planner"] = False

    if out["needs_clarification"] and not out["clarification_questions"]:
        out["clarification_questions"] = ["请补充关键约束后再继续（例如目标、范围、交付形式）。"]
    return out


def format_task_frame_block(frame: dict[str, Any] | None) -> str:
    if not frame:
        return "(无)"
    lines = [
        f"- task_mode: {frame.get('task_mode')}",
        f"- effort_level: {frame.get('effort_level')} · risk_level: {frame.get('risk_level')}",
        f"- deliverable_type: {frame.get('deliverable_type')}",
        f"- should_invoke_planner: {frame.get('should_invoke_planner')}",
    ]
    scopes = frame.get("allowed_action_scope") or []
    if isinstance(scopes, list) and scopes:
        lines.append(f"- allowed_action_scope: {', '.join(str(s) for s in scopes[:12])}")
    criteria = frame.get("success_criteria") or []
    if isinstance(criteria, list) and criteria:
        lines.append("- success_criteria:")
        for c in criteria[:8]:
            lines.append(f"  - {c}")
    rs = str(frame.get("reasoning_summary") or "").strip()
    if rs:
        lines.append(f"- reasoning: {rs[:300]}")
    return "\n".join(lines)


def deliverable_type_implies_artifact(deliverable_type: str) -> bool:
    dt = (deliverable_type or "").strip().lower()
    if dt in ("", "unspecified", "chat_answer", "direct_answer"):
        return False
    return True


async def task_frame_node(state: SessionState) -> SessionState:
    session_id = state["session_id"]
    run_id = state.get("run_id")
    user_message = state.get("user_message") or ""

    if not str(user_message).strip():
        log.info("graph.task_frame.blank_user", session_id=str(session_id))
        return {"task_frame": frame_for_blank_user_message()}

    if state.get("skip_planner"):
        tf = dict(DEFAULT_TASK_FRAME)
        tf["reasoning_summary"] = "skip_planner（API 未指定 planner，沿用全链路执行）"
        log.info("graph.task_frame.skipped", session_id=str(session_id), reason="skip_planner")
        return {"task_frame": tf}

    settings = get_settings()
    model = (state.get("planner_model") or settings.agent_default_planner).strip()
    template = load_template("task_frame", "v1")

    if not template:
        log.warning("graph.task_frame.template_missing")
        return {"task_frame": normalize_task_frame(None)}

    prompt = render(template, user_message=user_message)
    await emit(
        StatusEvent(
            session_id=session_id,
            run_id=run_id,
            phase=SessionPhase.planning,
            message="正在分析任务意图与执行模式",
        )
    )
    frame: dict[str, Any]
    try:
        client = get_async_openai()
        resp = await client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            response_format={"type": "json_object"},
            stream=False,
        )
        raw = (resp.choices[0].message.content or "").strip()
        parsed = _parse_frame_json(raw)
        frame = normalize_task_frame(parsed)
    except Exception as exc:  # noqa: BLE001
        log.warning("graph.task_frame.llm_failed", error=str(exc), model=model)
        frame = normalize_task_frame(None)
        frame["reasoning_summary"] = f"framing_llm_error: {exc}"

    log.info(
        "graph.task_frame.ready",
        session_id=str(session_id),
        mode=frame.get("task_mode"),
        planner=frame.get("should_invoke_planner"),
        clarify=frame.get("needs_clarification"),
    )
    return {"task_frame": frame}
