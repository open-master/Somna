"""plan node — produces a TODO list before the executor starts.

Uses the planner prompt template and the `agent-planner` model alias. Failure
is non-fatal: if the planner model returns unparseable JSON or errors out,
we log a warning and continue straight to execute with no plan.
"""

from __future__ import annotations

import json
import re
from typing import Any
from uuid import uuid4

from langchain_core.messages import SystemMessage
from somna_events import PlanUpdateEvent, SessionPhase, SkillDebugEvent, StatusEvent, TodoItem, TodoStatus

from app.config import get_settings
from app.events.emitter import emit
from app.graph.nodes.task_frame import format_task_frame_block
from app.graph.run_artifacts import persist_plan_pointer
from app.graph.state import SessionState
from app.graph.user_turn import last_human_turn_text
from app.llm.client import get_async_openai
from app.logging_setup import get_logger
from app.memory import format_memories, search_memories
from app.prompts.loader import load_template, render
from app.services.billing import emit_model_usage
from app.services.skill_router import SkillRouteResult, route_skills_for_task, selected_skills_payload
from app.tools.schema import tool_manifest_cache

log = get_logger(__name__)

_JSON_BLOCK_RE = re.compile(r"\{[\s\S]*\}", re.MULTILINE)


def _format_tools_line(manifests) -> str:
    return ", ".join(m.name for m in manifests) or "(暂无)"


def _completed_todos_block(plan: dict[str, Any] | None) -> str:
    todos = (plan or {}).get("todos") if isinstance(plan, dict) else None
    if not isinstance(todos, list):
        return "(无)"
    lines = []
    for item in todos:
        if not isinstance(item, dict) or str(item.get("status")) != TodoStatus.done:
            continue
        text = str(item.get("text") or "").strip()
        if text:
            lines.append(f"- {text}")
    return "\n".join(lines) or "(无)"


async def _emit_skill_debug_event(session_id, run_id: str | None, skill_route: SkillRouteResult) -> None:
    await emit(
        SkillDebugEvent(
            session_id=session_id,
            run_id=run_id,
            candidate_count=skill_route.candidate_count,
            selected_skills=selected_skills_payload(skill_route),
        )
    )


def _parse_plan(raw: str) -> dict[str, Any] | None:
    """Best-effort JSON extraction from planner response."""
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


def _coerce_todos(items: Any) -> list[TodoItem]:
    out: list[TodoItem] = []
    if not isinstance(items, list):
        return out
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or item.get("title") or "").strip()
        if not text:
            continue
        out.append(
            TodoItem(
                id=str(item.get("id") or len(out) + 1),
                text=text[:300],
                status=TodoStatus.pending,
                parent_id=item.get("parent_id"),
            )
        )
    return out


async def plan_node(state: SessionState) -> SessionState:
    settings = get_settings()
    session_id = state["session_id"]
    run_id = state.get("run_id")
    if state.get("skip_planner"):
        log.info("graph.plan.skipped", session_id=str(session_id), reason="skip_planner")
        return {"plan": None}

    planner_model = state.get("planner_model") or settings.agent_default_planner
    user_message = last_human_turn_text(state)

    manifests = list(tool_manifest_cache().values())
    skill_route = await route_skills_for_task(
        user_id=state.get("user_id"),
        user_message=user_message,
        task_frame=state.get("task_frame") if isinstance(state.get("task_frame"), dict) else None,
        plan=state.get("plan") if isinstance(state.get("plan"), dict) else None,
        skill_mode=state.get("skill_mode"),
        skill_model=state.get("skill_model"),
        session_id=session_id if state.get("user_id") else None,
        run_id=run_id,
        usage_key=f"{run_id}:skill_router:plan:{int(state.get('reflection_count') or 0)}",
    )
    selected_skills = selected_skills_payload(skill_route)
    skill_update = {
        "selected_skills": selected_skills,
        "skill_prompt_block": skill_route.prompt_block,
        "skill_candidate_count": skill_route.candidate_count,
        "skill_route_resolved": True,
    }
    await _emit_skill_debug_event(session_id, run_id, skill_route)

    template = load_template("planner", "v1")
    if not template:
        log.warning("graph.plan.template_missing")
        return {"plan": None, **skill_update}

    memories = await search_memories(
        user_message,
        session_id=str(session_id),
        user_id=state.get("user_id"),
    )
    memory_block = format_memories(memories) or "(无)"

    prompt = render(
        template,
        task_frame_block=format_task_frame_block(state.get("task_frame")),
        user_message=user_message,
        compact_memory=state.get("compact_memory") or "(无)",
        retrieved_memories=memory_block,
        completed_todos=_completed_todos_block(state.get("plan")),
        tools_list=_format_tools_line(manifests),
        selected_skills_context=skill_route.prompt_block or "(本轮未加载任何 Skill)",
    )
    reflection = state.get("reflection")
    if isinstance(reflection, dict) and str(reflection.get("reason") or "").strip():
        prompt += (
            "\n\n### 上一轮反思结论\n"
            f"原因：{reflection.get('reason')}\n"
            f"重点：{reflection.get('focus') or '(无)'}\n"
        )

    client = get_async_openai()
    await emit(
        StatusEvent(
            session_id=session_id,
            run_id=run_id,
            phase=SessionPhase.planning,
            message="正在生成任务计划",
        )
    )
    try:
        resp = await client.chat.completions.create(
            model=planner_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            response_format={"type": "json_object"},
            stream=False,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("graph.plan.llm_failed", error=str(exc), model=planner_model)
        return {"plan": None, **skill_update}

    raw = (resp.choices[0].message.content or "").strip()
    usage = getattr(resp, "usage", None)
    usage_input = int(getattr(usage, "prompt_tokens", 0) or 0)
    usage_output = int(getattr(usage, "completion_tokens", 0) or 0)
    if state.get("user_id") and (usage_input or usage_output):
        await emit_model_usage(
            session_id=session_id,
            run_id=run_id,
            usage_key=f"{run_id}:planner:{int(state.get('reflection_count') or 0)}",
            phase="planner",
            model=planner_model,
            input_tokens=usage_input,
            output_tokens=usage_output,
        )
    parsed = _parse_plan(raw)
    if not parsed:
        log.warning("graph.plan.unparseable", raw_preview=raw[:200])
        return {"plan": None, **skill_update}

    todos = _coerce_todos(parsed.get("todos"))
    if not todos:
        log.info("graph.plan.empty", reasoning=parsed.get("reasoning"))
        return {"plan": None, **skill_update}

    plan_id = f"plan_{uuid4().hex[:8]}"
    plan_obj = {
        "id": plan_id,
        "reasoning": parsed.get("reasoning"),
        "todos": [t.model_dump() for t in todos],
        "estimated_steps": parsed.get("estimated_steps"),
    }

    await emit(
        PlanUpdateEvent(
            session_id=session_id,
            run_id=run_id,
            todos=todos,
        )
    )
    await emit(
        StatusEvent(
            session_id=session_id,
            run_id=run_id,
            phase=SessionPhase.executing,
            message=f"计划已生成，共 {len(todos)} 步",
        )
    )

    log.info(
        "graph.plan.ready",
        session_id=str(session_id),
        plan_id=plan_id,
        n=len(todos),
        model=planner_model,
    )

    # Inject a compact plan summary for the executor as a system-level nudge.
    bullet_lines = "\n".join(f"{i + 1}. {t.text}" for i, t in enumerate(todos))
    nudge = SystemMessage(
        content=(
            "以下是刚刚为本任务生成的 TODO 列表，请按顺序执行；"
            "如果需要调整，先告诉用户再改动。\n\n" + bullet_lines
        )
    )
    new_messages = list(state.get("messages") or [])
    new_messages.append(nudge)

    plan_path = await persist_plan_pointer(state, plan_obj)

    return {"plan": plan_obj, "plan_path": plan_path, "messages": new_messages, **skill_update}


# ------------------------------------------------------------------
#  Progress helpers (used by execute_node)
# ------------------------------------------------------------------


def _plan_todos(plan: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not plan or not isinstance(plan, dict):
        return []
    items = plan.get("todos") or []
    return [dict(t) for t in items if isinstance(t, dict)]


def _rebuild_plan(plan: dict[str, Any] | None, todos: list[dict[str, Any]]) -> dict[str, Any]:
    base = dict(plan or {})
    base["todos"] = todos
    return base


def _todos_to_items(todos: list[dict[str, Any]]) -> list[TodoItem]:
    out: list[TodoItem] = []
    for t in todos:
        try:
            out.append(
                TodoItem(
                    id=str(t.get("id") or len(out) + 1),
                    text=str(t.get("text") or ""),
                    status=TodoStatus(t.get("status") or TodoStatus.pending),
                    parent_id=t.get("parent_id"),
                )
            )
        except Exception:  # noqa: BLE001
            continue
    return out


async def _emit_plan_update(*, session_id, run_id, todos: list[dict[str, Any]]) -> None:
    try:
        await emit(
            PlanUpdateEvent(
                session_id=session_id,
                run_id=run_id,
                todos=_todos_to_items(todos),
            )
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("graph.plan.progress_emit_failed", error=str(exc))


def _todo_requires_artifact_proof(todo: dict[str, Any]) -> bool:
    text = str(todo.get("text") or "").lower()
    direct_artifact_keywords = (
        "网站",
        "网页",
        "页面",
        "csv",
        "json",
        "报告",
        "文件",
        "下载",
        "artifact",
        "dashboard",
        "website",
    )
    if any(k in text for k in direct_artifact_keywords):
        return True

    build_intent_keywords = ("写", "生成", "创建", "产出", "制作", "开发", "搭建", "实现", "做")
    build_artifact_subjects = ("前端", "html", "react", "next.js", "项目", "代码", "脚本", "应用", "demo")
    return any(k in text for k in build_artifact_subjects) and any(k in text for k in build_intent_keywords)


def _record_todo_evidence(todo: dict[str, Any], proof: Any) -> bool:
    changed = False
    existing_paths = [str(p) for p in (todo.get("evidence_paths") or []) if isinstance(p, str)]
    seen = set(existing_paths)
    for path in list(getattr(proof, "written_paths", set()) or set()) + list(
        getattr(proof, "verified_paths", set()) or set()
    ):
        if not isinstance(path, str) or not path or path in seen:
            continue
        existing_paths.append(path)
        seen.add(path)
        changed = True
    if changed:
        todo["evidence_paths"] = existing_paths

    tool_call_count = int(todo.get("tool_call_count") or 0)
    delta_calls = int(getattr(proof, "successful_tool_calls", 0) or 0)
    if delta_calls > 0:
        todo["tool_call_count"] = tool_call_count + delta_calls
        changed = True
    return changed


async def advance_with_proof(
    plan: dict[str, Any] | None,
    *,
    session_id,
    run_id,
    proof: Any,
) -> dict[str, Any] | None:
    """Update the current todo using concrete execution proof from one turn.

    Generic todos may complete after any successful tool call.
    Artifact-producing todos require at least one path written by this run
    before advancing. Read/stat/list evidence may be recorded but cannot prove
    that the requested artifact was produced.
    """
    todos = _plan_todos(plan)
    if not todos:
        return plan

    current: dict[str, Any] | None = None
    for t in todos:
        if t.get("status") == TodoStatus.in_progress:
            current = t
            break
    if current is None:
        return plan

    changed = _record_todo_evidence(current, proof)
    has_success = int(getattr(proof, "successful_tool_calls", 0) or 0) > 0
    has_artifact_evidence = bool(getattr(proof, "written_paths", set()) or set())

    should_complete = has_success and (
        has_artifact_evidence if _todo_requires_artifact_proof(current) else True
    )
    if should_complete:
        current["status"] = TodoStatus.done
        changed = True
        for t in todos:
            if t.get("status") == TodoStatus.pending:
                t["status"] = TodoStatus.in_progress
                changed = True
                break

    if not changed:
        return plan

    new_plan = _rebuild_plan(plan, todos)
    await _emit_plan_update(session_id=session_id, run_id=run_id, todos=todos)
    return new_plan


async def mark_progress(
    plan: dict[str, Any] | None,
    *,
    session_id,
    run_id,
    complete_current: bool = False,
    start_next: bool = False,
    fail_current: bool = False,
    finish_all: bool = False,
    close_unfinished: bool = False,
) -> dict[str, Any] | None:
    """Mutate the plan's todo statuses and emit a PlanUpdateEvent.

    - `complete_current`: mark the first in_progress item as `done`.
    - `fail_current`: mark the first in_progress item as `failed`.
    - `start_next`: mark the first `pending` item as `in_progress`.
    - `finish_all`: mark every remaining `pending` / `in_progress` item as `done`.
    - `close_unfinished`: mark `in_progress` as `failed` and `pending` as `skipped`.
      Use only when the run must stop without proving the remaining work.
    Returns the new plan dict (or the original if nothing changed / no plan).
    """
    todos = _plan_todos(plan)
    if not todos:
        return plan

    changed = False

    if finish_all:
        for t in todos:
            if t.get("status") in {TodoStatus.pending, TodoStatus.in_progress}:
                t["status"] = TodoStatus.done
                changed = True

    if close_unfinished:
        for t in todos:
            if t.get("status") == TodoStatus.in_progress:
                t["status"] = TodoStatus.failed
                changed = True
            elif t.get("status") == TodoStatus.pending:
                t["status"] = TodoStatus.skipped
                changed = True

    if (complete_current or fail_current) and any(
        t.get("status") == TodoStatus.in_progress for t in todos
    ):
        target_status = TodoStatus.failed if fail_current else TodoStatus.done
        for t in todos:
            if t.get("status") == TodoStatus.in_progress:
                t["status"] = target_status
                changed = True
                break

    if start_next:
        if not any(t.get("status") == TodoStatus.in_progress for t in todos):
            for t in todos:
                if t.get("status") == TodoStatus.pending:
                    t["status"] = TodoStatus.in_progress
                    changed = True
                    break

    if not changed:
        return plan

    new_plan = _rebuild_plan(plan, todos)
    await _emit_plan_update(session_id=session_id, run_id=run_id, todos=todos)
    return new_plan
