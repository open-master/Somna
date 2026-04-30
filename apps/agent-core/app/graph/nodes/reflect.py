"""reflect node — review the latest execute pass and decide next hop."""

from __future__ import annotations

import json
import re
from typing import Any

from langchain_core.messages import SystemMessage
from somna_events import SessionPhase, StatusEvent

from app.config import get_settings
from app.events.emitter import emit
from app.graph.nodes.plan import mark_progress
from app.graph.state import SessionState
from app.llm.client import get_async_openai
from app.logging_setup import get_logger
from app.prompts.loader import load_template, render

log = get_logger(__name__)

_JSON_BLOCK_RE = re.compile(r"\{[\s\S]*\}", re.MULTILINE)
_MAX_REFLECTIONS = 3


def _is_mode2_executor(state: SessionState) -> bool:
    """模式二：Anthropic Messages 协议（与 post_message executor_engine 一致）。"""
    eng = (state.get("executor_engine") or "native").strip().lower()
    return eng in ("anthropic", "anthropic_compat", "mode2")


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


def _fallback_decision(state: SessionState) -> dict[str, Any]:
    summary = state.get("execution_summary") or {}
    plan = state.get("plan")
    reason = str(summary.get("delivery_missing_reason") or "").strip()
    reflections = int(state.get("reflection_count") or 0)

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
    if not (state.get("assistant_text") or "").strip():
        return {"decision": "continue_execute", "reason": "当前没有形成有效回答", "focus": "继续执行并形成有效结论"}
    pending = [
        t
        for t in ((plan or {}).get("todos") or [])
        if isinstance(t, dict) and str(t.get("status")) in {"pending", "in_progress"}
    ]
    if pending:
        return {"decision": "continue_execute", "reason": "仍有未完成 TODO", "focus": str(pending[0].get("text") or "")}
    return {"decision": "finalize", "reason": "当前结果已满足结束条件", "focus": ""}


async def reflect_node(state: SessionState) -> SessionState:
    session_id = state["session_id"]
    run_id = state.get("run_id")
    if state.get("error"):
        return {"next_node": "finalize"}

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
    prompt = render(
        template,
        session_goal=state.get("user_message") or "",
        assistant_text=state.get("assistant_text") or "(无)",
        current_todos=_todo_block(state.get("plan")),
        completed_todos=_todo_block(state.get("plan"), status="done"),
        artifacts=_artifact_block(state.get("plan"), summary),
        execution_summary=json.dumps(summary, ensure_ascii=False, indent=2)[:4000] if summary else "(无)",
        compact_memory=state.get("compact_memory") or "(无)",
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
                decision = parsed
    except Exception as exc:  # noqa: BLE001
        log.warning("graph.reflect.llm_failed", error=str(exc))

    route = str(decision.get("decision") or "finalize")
    reason = str(decision.get("reason") or "").strip()
    focus = str(decision.get("focus") or "").strip()

    if reflections >= _MAX_REFLECTIONS and route != "finalize":
        route = "finalize"
        reason = reason or "已达到反思上限，结束本轮"
        focus = ""

    plan = state.get("plan")
    messages = list(state.get("messages") or [])

    if route == "finalize":
        if _is_mode2_executor(state):
            # 模式一在 finalize 时仍用 finish_all 勾选剩余步骤；模式二则保留 execute/advance 的真实状态，
            # 避免「工具失败仍显示全完成」。
            log.info(
                "graph.reflect.finalize_mode2",
                session_id=str(session_id),
                run_id=run_id,
            )
        else:
            plan = await mark_progress(plan, session_id=session_id, run_id=run_id, finish_all=True)
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
            "skip_planner": False,
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
