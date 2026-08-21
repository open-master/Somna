"""reflect node — review the latest execute pass and decide next hop."""

from __future__ import annotations

import json
import re
from typing import Any

from langchain_core.messages import SystemMessage
from somna_events import SessionPhase, StatusEvent

from app.config import get_settings
from app.events.emitter import emit
from app.graph.autonomy_policy import effective_autonomy_level
from app.graph.nodes.plan import mark_progress
from app.graph.nodes.task_frame import deliverable_type_implies_artifact
from app.graph.state import SessionState
from app.llm.client import get_async_openai
from app.logging_setup import get_logger
from app.prompts.loader import load_template, render
from app.services.billing import emit_model_usage

log = get_logger(__name__)

_JSON_BLOCK_RE = re.compile(r"\{[\s\S]*\}", re.MULTILINE)
_MAX_REFLECTIONS = 3


def _parse_reflection(raw: str) -> dict[str, Any] | None:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    match = _JSON_BLOCK_RE.search(raw)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def _todo_block(plan: dict[str, Any] | None, *, status: str | None = None) -> str:
    todos = (plan or {}).get("todos") if isinstance(plan, dict) else None
    if not isinstance(todos, list):
        return "(无)"
    lines = []
    for item in todos:
        if not isinstance(item, dict):
            continue
        if status and str(item.get("status")) != status:
            continue
        text = str(item.get("text") or "").strip()
        if text:
            lines.append(f"- {text}")
    return "\n".join(lines) or "(无)"


def _artifact_block(plan: dict[str, Any] | None, execution_summary: dict[str, Any] | None) -> str:
    lines: list[str] = []
    todos = (plan or {}).get("todos") if isinstance(plan, dict) else []
    if isinstance(todos, list):
        for item in todos:
            if not isinstance(item, dict):
                continue
            text = str(item.get("text") or "").strip()
            paths = [p for p in (item.get("evidence_paths") or []) if isinstance(p, str)]
            if text and paths:
                lines.append(f"- {text}: {', '.join(paths[:5])}")
    if execution_summary:
        for key in ("written_paths", "verified_paths"):
            paths = execution_summary.get(key) or []
            if isinstance(paths, list) and paths:
                lines.append(f"- {key}: {', '.join(str(p) for p in paths[:8])}")
    return "\n".join(lines) or "(无)"


def _selected_skills_block(state: SessionState) -> str:
    rows = state.get("selected_skills") or []
    if not isinstance(rows, list) or not rows:
        return "(本轮未选中 Skill)"
    lines: list[str] = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        files = item.get("load_files") if isinstance(item.get("load_files"), list) else []
        file_text = ", ".join(str(x) for x in files[:8] if isinstance(x, str)) or "无"
        forced = "；Task Frame 强制注入" if item.get("forced") else ""
        lines.append(f"- {name}{forced}；注入文件：{file_text}")
    return "\n".join(lines) or "(本轮未选中 Skill)"


def _has_artifact_evidence(summary: dict[str, Any]) -> bool:
    for key in ("written_paths", "verified_paths"):
        paths = summary.get(key) or []
        if isinstance(paths, list) and any(isinstance(p, str) and p.strip() for p in paths):
            return True
    return False


def _should_block_skill_finalize(state: SessionState) -> tuple[bool, str, str]:
    if not (state.get("selected_skills") or []):
        return False, "", ""
    pending = _pending_todos(state.get("plan"))
    if pending:
        return True, "已选中 Skill，但仍有未完成 TODO", str(pending[0].get("text") or "")
    tf = state.get("task_frame") if isinstance(state.get("task_frame"), dict) else {}
    summary = state.get("execution_summary") or {}
    if deliverable_type_implies_artifact(str(tf.get("deliverable_type") or "")) and not _has_artifact_evidence(summary):
        return True, "已选中 Skill 且任务要求交付文件，但缺少产物证据", "继续按 Skill workflow 生成并验证交付文件"
    return False, "", ""


def _pending_todos(plan: dict[str, Any] | None) -> list[dict[str, Any]]:
    return [
        t
        for t in ((plan or {}).get("todos") or [])
        if isinstance(t, dict) and str(t.get("status")) in {"pending", "in_progress"}
    ]


def _fallback_decision(state: SessionState) -> dict[str, Any]:
    summary = state.get("execution_summary") or {}
    plan = state.get("plan")
    reason = str(summary.get("delivery_missing_reason") or "").strip()
    reflections = int(state.get("reflection_count") or 0)
    tf = state.get("task_frame") if isinstance(state.get("task_frame"), dict) else {}
    eff = effective_autonomy_level(tf)

    if reflections >= _MAX_REFLECTIONS:
        return {
            "decision": "finalize",
            "reason": "已达到反思上限，避免无限循环",
            "focus": "",
        }
    if reason:
        if "多步计划" in reason or "步骤" in reason:
            return {"decision": "replan", "reason": reason, "focus": "基于当前结果重新拆解剩余步骤"}
        return {"decision": "continue_execute", "reason": reason, "focus": "补齐缺失的真实执行和验证"}
    blocked, block_reason, focus = _should_block_skill_finalize(state)
    if blocked:
        return {"decision": "continue_execute", "reason": block_reason, "focus": focus}
    if not (state.get("assistant_text") or "").strip():
        return {"decision": "continue_execute", "reason": "当前没有形成有效回答", "focus": "继续执行并形成有效结论"}
    pending = _pending_todos(plan)
    if pending:
        return {"decision": "continue_execute", "reason": "仍有未完成 TODO", "focus": str(pending[0].get("text") or "")}

    # 低自主 + 首轮反思：若定调写了验收标准，多给一轮执行做对照（避免过早 finalize）。
    sc = tf.get("success_criteria") if isinstance(tf.get("success_criteria"), list) else []
    sc_texts = [str(x).strip() for x in sc if str(x).strip()]
    if eff == "low" and reflections == 0 and sc_texts:
        return {
            "decision": "continue_execute",
            "reason": "低自主：定调含 success_criteria，需再自检一轮",
            "focus": "；".join(sc_texts[:4]),
        }

    return {"decision": "finalize", "reason": "当前结果已满足结束条件", "focus": ""}


def _coerce_route_for_high_autonomy(state: SessionState, route: str, reason: str, focus: str) -> tuple[str, str, str]:
    """高自主：若无交付缺口且无待办、已有回答，则将多余的 continue_execute 收为 finalize。"""
    if route != "continue_execute":
        return route, reason, focus
    if effective_autonomy_level(state.get("task_frame") if isinstance(state.get("task_frame"), dict) else None) != "high":
        return route, reason, focus
    summary = state.get("execution_summary") or {}
    if str(summary.get("delivery_missing_reason") or "").strip():
        return route, reason, focus
    if _pending_todos(state.get("plan")):
        return route, reason, focus
    if not (state.get("assistant_text") or "").strip():
        return route, reason, focus
    return (
        "finalize",
        reason or "高自主：无未完成项与交付缺口，收口结束",
        "",
    )


def _autonomy_audit_line(state: SessionState) -> tuple[str, str, str]:
    tf = state.get("task_frame") if isinstance(state.get("task_frame"), dict) else {}
    declared = str(tf.get("autonomy_level") or "medium").strip().lower()
    risk = str(tf.get("risk_level") or "low").strip().lower()
    eff = effective_autonomy_level(tf)
    return eff, declared if declared in ("low", "medium", "high") else "medium", risk if risk in ("low", "medium", "high") else "low"


async def reflect_node(state: SessionState) -> SessionState:
    session_id = state["session_id"]
    run_id = state.get("run_id")
    if state.get("error"):
        return {"next_node": "finalize"}
    settings = get_settings()
    if (
        int(state.get("tool_turns") or 0) >= max(1, int(settings.agent_max_turns))
        or int(state.get("total_agent_turns") or 0) >= max(1, int(settings.agent_max_total_turns))
        or int(state.get("total_execution_tokens") or 0)
        >= max(1, int(settings.agent_max_total_tokens))
    ):
        plan = await mark_progress(
            state.get("plan"),
            session_id=session_id,
            run_id=run_id,
            close_unfinished=True,
        )
        return {
            "plan": plan,
            "reflection": {
                "decision": "finalize",
                "reason": "已达到本轮全局执行预算上限",
                "focus": "",
                "raw": "",
            },
            "reflection_count": int(state.get("reflection_count") or 0),
            "next_node": "finalize",
        }

    reflections = int(state.get("reflection_count") or 0) + 1
    await emit(
        StatusEvent(
            session_id=session_id,
            run_id=run_id,
            phase=SessionPhase.executing,
            message="正在复盘本轮执行结果",
        )
    )

    template = load_template("reflect", "v1")
    summary = state.get("execution_summary") or {}
    eff_a, declared_a, risk_a = _autonomy_audit_line(state)
    autonomy_playbook = (
        f"当前 effective_autonomy={eff_a}（声明 {declared_a}，risk={risk_a}；"
        "high risk 时 autonomy 上限为 medium）。"
        "low = 宁可多轮执行/重规划也别过早 finalize；"
        "high = 无缺口时可果断 finalize。"
    )
    prompt = render(
        template,
        session_goal=state.get("user_message") or "",
        assistant_text=state.get("assistant_text") or "(无)",
        current_todos=_todo_block(state.get("plan")),
        completed_todos=_todo_block(state.get("plan"), status="done"),
        artifacts=_artifact_block(state.get("plan"), summary),
        selected_skills_context=_selected_skills_block(state),
        execution_summary=json.dumps(summary, ensure_ascii=False, indent=2)[:4000] if summary else "(无)",
        compact_memory=state.get("compact_memory") or "(无)",
        effective_autonomy=eff_a,
        autonomy_playbook=autonomy_playbook,
    )

    decision = _fallback_decision(state)
    raw = ""
    try:
        if template:
            client = get_async_openai()
            _reasoner = (state.get("reasoner_model") or get_settings().agent_default_reasoner).strip()
            log.info(
                "graph.reflect.start",
                session_id=str(session_id),
                run_id=run_id,
                model=_reasoner,
            )
            resp = await client.chat.completions.create(
                model=_reasoner,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                response_format={"type": "json_object"},
                stream=False,
            )
            raw = (resp.choices[0].message.content or "").strip()
            parsed = _parse_reflection(raw)
            if isinstance(parsed, dict) and parsed.get("decision") in {
                "finalize",
                "continue_execute",
                "replan",
            }:
                usage = getattr(resp, "usage", None)
                usage_input = int(getattr(usage, "prompt_tokens", 0) or 0)
                usage_output = int(getattr(usage, "completion_tokens", 0) or 0)
                if state.get("user_id") and (usage_input or usage_output):
                    await emit_model_usage(
                        session_id=session_id,
                        run_id=run_id,
                        usage_key=f"{run_id}:reflect:{reflections}",
                        phase="reflect",
                        model=_reasoner,
                        input_tokens=usage_input,
                        output_tokens=usage_output,
                    )
                decision = parsed
    except Exception as exc:  # noqa: BLE001
        log.warning("graph.reflect.llm_failed", error=str(exc))

    route = str(decision.get("decision") or "finalize")
    reason = str(decision.get("reason") or "").strip()
    focus = str(decision.get("focus") or "").strip()

    blocked, block_reason, block_focus = _should_block_skill_finalize(state)
    if route == "finalize" and blocked:
        route = "continue_execute"
        reason = block_reason
        focus = block_focus

    # A model decision must not make the UI claim that unfinished plan items
    # are complete. Keep executing while a retry budget remains; when the
    # reflection cap is reached, finalize truthfully with failed/skipped items.
    pending_before_finalize = _pending_todos(state.get("plan"))
    if route == "finalize" and pending_before_finalize and reflections < _MAX_REFLECTIONS:
        route = "continue_execute"
        reason = "仍有未完成 TODO，暂不能宣告任务完成"
        focus = str(pending_before_finalize[0].get("text") or "继续完成并验证剩余步骤")

    route, reason, focus = _coerce_route_for_high_autonomy(state, route, reason, focus)
    if route == "finalize" and blocked:
        route = "continue_execute"
        reason = block_reason
        focus = block_focus

    if reflections >= _MAX_REFLECTIONS and route != "finalize":
        route = "finalize"
        reason = reason or "已达到反思上限，结束本轮"
        focus = ""

    log.info(
        "graph.reflect.decision",
        session_id=str(session_id),
        run_id=run_id,
        route=route,
        reflections=reflections,
        effective_autonomy=eff_a,
        declared_autonomy=declared_a,
        risk_level=risk_a,
    )

    plan = state.get("plan")
    messages = list(state.get("messages") or [])

    if route == "finalize":
        unfinished = _pending_todos(plan)
        if unfinished:
            # Reaching finalize with unfinished work means the retry/reflection
            # budget is exhausted. Preserve truth in the timeline instead of
            # force-marking every remaining item as done.
            plan = await mark_progress(
                plan,
                session_id=session_id,
                run_id=run_id,
                close_unfinished=True,
            )
            log.info(
                "graph.reflect.finalize_with_unfinished",
                session_id=str(session_id),
                run_id=run_id,
                unfinished=len(unfinished),
            )
        return {
            "plan": plan,
            "reflection": {"decision": route, "reason": reason, "focus": focus, "raw": raw},
            "reflection_count": reflections,
            "next_node": "finalize",
        }

    if route == "replan":
        messages.append(
            SystemMessage(
                content=(
                    "反思结论：当前方案需要重新规划。\n"
                    f"原因：{reason or '现有计划不足以完成任务'}\n"
                    f"重规划重点：{focus or '结合当前结果重新拆解剩余步骤'}"
                )
            )
        )
        return {
            "messages": messages,
            "reflection": {"decision": route, "reason": reason, "focus": focus, "raw": raw},
            "reflection_count": reflections,
            "next_node": "plan",
            "skip_planner": bool(state.get("skip_planner")),
        }

    messages.append(
        SystemMessage(
            content=(
                "反思结论：任务尚未完成，请继续执行。\n"
                f"原因：{reason or '需要补齐缺失步骤'}\n"
                f"优先处理：{focus or '补齐剩余执行与验证'}"
            )
        )
    )
    return {
        "messages": messages,
        "reflection": {"decision": route, "reason": reason, "focus": focus, "raw": raw},
        "reflection_count": reflections,
        "next_node": "execute",
    }
