"""Task framing — execution mode and routing before planner (phase A)."""

from __future__ import annotations

import json
import re
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage
from somna_events import SessionPhase, StatusEvent, TaskFrameEvent

from app.config import get_settings
from app.events.emitter import emit
from app.graph.run_artifacts import persist_task_frame_pointer
from app.graph.state import SessionState
from app.graph.user_turn import last_human_turn_text
from app.llm.client import get_async_openai
from app.logging_setup import get_logger
from app.prompts.loader import load_template, render
from app.services.skills import list_enabled_skill_candidates

log = get_logger(__name__)

_JSON_BLOCK_RE = re.compile(r"\{[\s\S]*\}", re.MULTILINE)

# 用户明确要求检索/时效/产出时，不要用启发式压低 planner。
_RESEARCH_INTENT_RE = re.compile(
    r"(搜索|检索|联网|查找|查一下|最新|近况|实时|新闻|今天|引用|来源|链接|论文|"
    r"资料整理|调研|报告|摘要|汇总|对比|统计数据|verify|文献)",
    re.IGNORECASE,
)

_SIMPLE_DEF_Q_RE = re.compile(
    r"(是谁|是什么|什么意思|是哪个|指什么|在哪出生|干什么的|干嘛的)[？?！!…。\s]*$",
)

DEFAULT_TASK_FRAME: dict[str, Any] = {
    "needs_clarification": False,
    "clarification_questions": [],
    "task_mode": "full_pipeline",
    "effort_level": "medium",
    "autonomy_level": "medium",
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


def _lc_message_text(m: Any) -> str:
    c = getattr(m, "content", "")
    if isinstance(c, str):
        return c.strip()
    if isinstance(c, list):
        parts: list[str] = []
        for p in c:
            if isinstance(p, dict) and p.get("type") == "text":
                parts.append(str(p.get("text", "")))
            else:
                parts.append(str(p))
        return "\n".join(parts).strip()
    return str(c).strip()


def _prior_messages_for_framing(messages: list[Any]) -> list[Any]:
    """本轮用户输入在 `user_message` 与 messages 末条 Human 重复；定调上文不含末条 Human。"""
    if not messages:
        return []
    last = messages[-1]
    if isinstance(last, HumanMessage):
        return list(messages[:-1])
    return list(messages)


def format_conversation_context_for_framing(messages: list[Any], *, max_chars: int = 8000) -> str:
    """供任务定调模型阅读的简体对话摘录。"""
    lines: list[str] = []
    for m in messages:
        if isinstance(m, HumanMessage):
            t = _lc_message_text(m)
            if t:
                lines.append(f"用户：{t}")
        elif isinstance(m, AIMessage):
            t = _lc_message_text(m)
            if t:
                cap = 2800
                if len(t) > cap:
                    t = t[:cap] + "…"
                lines.append(f"助手：{t}")
    if not lines:
        return "（尚无更早对话；本轮为首次输入。）"
    blob = "\n".join(lines)
    if len(blob) > max_chars:
        blob = "…\n" + blob[-max_chars:]
    return blob


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
    _al = str(parsed.get("autonomy_level") or "").strip().lower()
    if _al in ("low", "medium", "high"):
        out["autonomy_level"] = _al
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


def _maybe_coerce_simple_definitional_qa(user_message: str, frame: dict[str, Any]) -> None:
    """若模型误判常识短问为需规划/检索，则压低为直接回答（仅作兜底，不改变需澄清分支）。"""
    if frame.get("needs_clarification"):
        return
    msg = (user_message or "").strip()
    if not msg or len(msg) > 48:
        return
    if _RESEARCH_INTENT_RE.search(msg):
        return
    if not _SIMPLE_DEF_Q_RE.search(msg):
        return
    if not frame.get("should_invoke_planner"):
        return
    frame["should_invoke_planner"] = False
    frame["task_mode"] = "direct_answer"
    frame["deliverable_type"] = "chat_answer"
    frame["allowed_action_scope"] = []
    rs = str(frame.get("reasoning_summary") or "").strip()
    suffix = "coerced_simple_definitional_QA"
    frame["reasoning_summary"] = f"{rs}；{suffix}" if rs else suffix


def format_task_frame_block(frame: dict[str, Any] | None) -> str:
    if not frame:
        return "(无)"
    lines = [
        f"- task_mode: {frame.get('task_mode')}",
        f"- effort_level: {frame.get('effort_level')} · autonomy_level: {frame.get('autonomy_level')} · risk_level: {frame.get('risk_level')}",
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


def format_task_frame_ui_summary(frame: dict[str, Any] | None) -> str:
    """一行中文，用于 Web 顶栏「任务定调」摘要。"""
    if not frame:
        return "任务定调：无法解析，已使用默认策略。"
    rs = str(frame.get("reasoning_summary") or "")
    if rs == _BLANK_USER_REASON:
        return "任务定调：未识别到有效描述，将向您追问。"
    if "skip_planner" in rs:
        return "任务定调：未启用规划模型配置，跳过大模型定调，将直接走规划与执行。"
    if frame.get("needs_clarification"):
        return "任务定调：需要先确认若干信息后再继续。"
    if not frame.get("should_invoke_planner", True):
        return "任务定调：轻量直接回答，不进入多步规划与工具编排。"
    return "任务定调：将进入任务规划与工具执行。"


async def _format_enabled_skills_for_framing(state: SessionState) -> str:
    if (state.get("skill_mode") or "auto").strip().lower() == "off":
        return "（Skill 自动使用已关闭）"
    candidates = await list_enabled_skill_candidates(user_id=state.get("user_id"))
    if not candidates:
        return "（当前用户没有可用于任务执行的已启用 Skill）"
    lines: list[str] = []
    for item in candidates[:30]:
        name = str(item.get("name") or "").strip()
        desc = str(item.get("description") or "").strip()
        visibility = str(item.get("visibility") or "").strip()
        if name:
            lines.append(f"- `{name}` ({visibility}): {desc[:240]}")
    return "\n".join(lines) or "（当前用户没有可用于任务执行的已启用 Skill）"


async def _emit_task_frame_ui(session_id, run_id: str | None, frame: dict[str, Any]) -> None:
    await emit(
        TaskFrameEvent(
            session_id=session_id,
            run_id=run_id,
            summary=format_task_frame_ui_summary(frame),
            detail=format_task_frame_block(frame),
        )
    )


def deliverable_type_implies_artifact(deliverable_type: str) -> bool:
    dt = (deliverable_type or "").strip().lower()
    if dt in ("", "unspecified", "chat_answer", "direct_answer"):
        return False
    return True


async def task_frame_node(state: SessionState) -> SessionState:
    session_id = state["session_id"]
    run_id = state.get("run_id")
    user_message = last_human_turn_text(state)

    if not str(user_message).strip():
        log.info("graph.task_frame.blank_user", session_id=str(session_id))
        frame_b = frame_for_blank_user_message()
        await _emit_task_frame_ui(session_id, run_id, frame_b)
        path = await persist_task_frame_pointer(state, frame_b)
        return {"task_frame": frame_b, "task_frame_path": path}

    if state.get("skip_planner"):
        tf = dict(DEFAULT_TASK_FRAME)
        tf["reasoning_summary"] = "skip_planner（API 未指定 planner，沿用全链路执行）"
        log.info("graph.task_frame.skipped", session_id=str(session_id), reason="skip_planner")
        await _emit_task_frame_ui(session_id, run_id, tf)
        path = await persist_task_frame_pointer(state, tf)
        return {"task_frame": tf, "task_frame_path": path}

    settings = get_settings()
    model = (state.get("task_frame_model") or settings.agent_default_taskframe).strip()
    template = load_template("task_frame", "v1")

    if not template:
        log.warning("graph.task_frame.template_missing")
        frame = normalize_task_frame(None)
        _maybe_coerce_simple_definitional_qa(user_message, frame)
        await _emit_task_frame_ui(session_id, run_id, frame)
        path = await persist_task_frame_pointer(state, frame)
        return {"task_frame": frame, "task_frame_path": path}

    prior = _prior_messages_for_framing(list(state.get("messages") or []))
    conv_ctx = format_conversation_context_for_framing(prior)
    enabled_skills_context = await _format_enabled_skills_for_framing(state)
    prompt = render(
        template,
        user_message=user_message,
        conversation_context=conv_ctx,
        enabled_skills_context=enabled_skills_context,
    )
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

    _maybe_coerce_simple_definitional_qa(user_message, frame)
    log.info(
        "graph.task_frame.ready",
        session_id=str(session_id),
        mode=frame.get("task_mode"),
        planner=frame.get("should_invoke_planner"),
        clarify=frame.get("needs_clarification"),
    )
    await _emit_task_frame_ui(session_id, run_id, frame)
    path = await persist_task_frame_pointer(state, frame)
    return {"task_frame": frame, "task_frame_path": path}
