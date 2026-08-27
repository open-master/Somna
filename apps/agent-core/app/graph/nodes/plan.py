"""plan node — produces a TODO list before the executor starts.

Uses the planner prompt template and the `agent-planner` model alias. Failure
is non-fatal: if the planner model errors or returns unusable JSON, we emit a
minimal TODO list derived from the task frame instead of continuing with no plan.
"""

from __future__ import annotations

import json
import re
from fnmatch import fnmatch
from pathlib import Path
from typing import Any
from uuid import uuid4

from langchain_core.messages import SystemMessage
from somna_events import PlanUpdateEvent, SessionPhase, SkillDebugEvent, StatusEvent, TodoItem, TodoStatus

from app.config import get_settings
from app.events.emitter import emit
from app.graph.nodes.task_frame import (
    _coerce_clarification_questions,
    _emit_task_frame_ui,
    deliverable_type_implies_artifact,
    format_task_frame_block,
)
from app.graph.run_artifacts import persist_plan_pointer, persist_task_frame_pointer
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


def _status_value(raw: Any) -> str:
    if isinstance(raw, TodoStatus):
        return raw.value
    return str(raw or "")


def _completed_todos_block(plan: dict[str, Any] | None) -> str:
    todos = (plan or {}).get("todos") if isinstance(plan, dict) else None
    if not isinstance(todos, list):
        return "(无)"
    lines = []
    for item in todos:
        if not isinstance(item, dict) or _status_value(item.get("status")) != TodoStatus.done.value:
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
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass
    match = _JSON_BLOCK_RE.search(raw)
    if not match:
        return None
    try:
        parsed = json.loads(match.group(0))
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        return None


def _coerce_str_list(raw: Any, *, limit: int = 12, item_limit: int = 300) -> list[str]:
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for value in raw:
        text = str(value or "").strip()
        if text and text not in out:
            out.append(text[:item_limit])
        if len(out) >= limit:
            break
    return out


def _todo_text_key(text: str) -> str:
    return re.sub(r"[\W_]+", "", (text or "").lower(), flags=re.UNICODE)


def _coerce_nonnegative_int(raw: Any, *, default: int = 0) -> int:
    try:
        return max(0, int(raw or 0))
    except (TypeError, ValueError, OverflowError):
        return max(0, default)


def _coerce_todos(items: Any, *, preserve_status: bool = True) -> list[TodoItem]:
    """Normalize planner TODOs and enforce a deterministic sequential chain.

    The planner may suggest a dependency graph, but Somna currently promises
    ordered execution in the UI. Every item therefore depends on its immediate
    predecessor in addition to any valid earlier dependencies supplied by the
    planner. Dependency/tool/evidence fields must survive the planner boundary.
    """
    out: list[TodoItem] = []
    if not isinstance(items, list):
        return out
    used_ids: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or item.get("title") or "").strip()
        if not text:
            continue
        raw_id = str(item.get("id") or len(out) + 1).strip() or str(len(out) + 1)
        todo_id = raw_id
        suffix = 2
        while todo_id in used_ids:
            todo_id = f"{raw_id}_{suffix}"
            suffix += 1
        used_ids.add(todo_id)

        earlier_ids = {todo.id for todo in out}
        depends_on = [
            dep
            for dep in _coerce_str_list(item.get("depends_on"), limit=20, item_limit=80)
            if dep in earlier_ids
        ]
        if out and out[-1].id not in depends_on:
            depends_on.append(out[-1].id)

        status = TodoStatus.pending
        if preserve_status:
            try:
                status = TodoStatus(item.get("status") or TodoStatus.pending)
            except (TypeError, ValueError):
                status = TodoStatus.pending
        out.append(
            TodoItem(
                id=todo_id,
                text=text[:300],
                status=status,
                parent_id=(str(item.get("parent_id")).strip() or None)
                if item.get("parent_id") is not None
                else None,
                depends_on=depends_on,
                tool_hint=(str(item.get("tool_hint") or "").strip() or None),
                acceptance_criteria=_coerce_str_list(
                    item.get("acceptance_criteria") or item.get("acceptance") or item.get("success_criteria"),
                    limit=8,
                ),
                expected_outputs=_coerce_str_list(item.get("expected_outputs"), limit=12),
                evidence_paths=_coerce_str_list(item.get("evidence_paths"), limit=50, item_limit=500),
                tool_call_count=_coerce_nonnegative_int(item.get("tool_call_count")),
                attempts=_coerce_nonnegative_int(item.get("attempts")),
                completion_reason=(str(item.get("completion_reason") or "").strip() or None),
                failure_reason=(str(item.get("failure_reason") or "").strip() or None),
            )
        )
    return out


def _reconcile_completed_todos(todos: list[TodoItem], previous_plan: dict[str, Any] | None) -> list[TodoItem]:
    """Carry exact completed work and its evidence across a replan."""
    previous = (previous_plan or {}).get("todos") if isinstance(previous_plan, dict) else None
    if not isinstance(previous, list):
        return todos
    completed: dict[str, dict[str, Any]] = {}
    for item in previous:
        if not isinstance(item, dict) or _status_value(item.get("status")) != TodoStatus.done.value:
            continue
        key = _todo_text_key(str(item.get("text") or ""))
        if key:
            completed[key] = item
    for todo in todos:
        old = completed.get(_todo_text_key(todo.text))
        if not old:
            continue
        todo.status = TodoStatus.done
        todo.evidence_paths = _coerce_str_list(old.get("evidence_paths"), limit=50, item_limit=500)
        todo.tool_call_count = _coerce_nonnegative_int(old.get("tool_call_count"))
        todo.attempts = _coerce_nonnegative_int(old.get("attempts"))
        todo.completion_reason = str(old.get("completion_reason") or "已在上一版计划中完成")
        todo.failure_reason = None
    return todos


def _previous_plan_for_current_run(state: SessionState) -> dict[str, Any] | None:
    """Only an in-run replan may inherit completion evidence."""
    previous = state.get("plan") if isinstance(state.get("plan"), dict) else None
    run_id = str(state.get("run_id") or "").strip()
    if not previous or not run_id or str(previous.get("run_id") or "").strip() != run_id:
        return None
    return previous


def _fallback_plan_from_task_frame(
    *,
    user_message: str,
    task_frame: dict[str, Any] | None,
) -> dict[str, Any]:
    """Minimal TODOs from framing when the planner LLM fails. Not a new planner."""
    tf = task_frame if isinstance(task_frame, dict) else {}
    todos: list[TodoItem] = []
    seen: set[str] = set()

    def _add(text: str, *, acceptance_criteria: list[str] | None = None) -> None:
        cleaned = " ".join(text.split()).strip()[:300]
        if not cleaned or cleaned in seen or len(todos) >= 4:
            return
        seen.add(cleaned)
        todos.append(
            TodoItem(
                id=str(len(todos) + 1),
                text=cleaned,
                status=TodoStatus.pending,
                acceptance_criteria=acceptance_criteria or [],
            )
        )

    criteria = _coerce_str_list(tf.get("success_criteria"), limit=5)

    goal = (user_message or "").strip()
    if len(goal) > 80:
        goal = goal[:80].rstrip() + "…"
    _add(f"执行任务：{goal}" if goal else "完成本轮任务", acceptance_criteria=criteria)
    if deliverable_type_implies_artifact(str(tf.get("deliverable_type") or "")):
        _add("验证最终交付文件已写入沙盒且满足验收标准", acceptance_criteria=criteria)
    else:
        _add("整理并输出最终结果", acceptance_criteria=criteria)

    return {
        "id": f"plan_fallback_{uuid4().hex[:8]}",
        "reasoning": "planner 失败，按任务定调生成最小 TODO",
        "todos": [t.model_dump(mode="json") for t in todos],
        "estimated_steps": len(todos),
        "fallback_from_task_frame": True,
    }


async def _publish_plan(
    state: SessionState,
    plan_obj: dict[str, Any],
    skill_update: dict[str, Any],
) -> SessionState:
    session_id = state["session_id"]
    run_id = state.get("run_id")
    todos = _coerce_todos(plan_obj.get("todos"))
    plan_obj = dict(plan_obj)
    plan_obj["todos"] = [todo.model_dump(mode="json") for todo in todos]
    plan_obj["version"] = max(1, _coerce_nonnegative_int(plan_obj.get("version"), default=1))
    plan_obj["run_id"] = str(run_id or "") or None
    await emit(
        PlanUpdateEvent(
            session_id=session_id,
            run_id=run_id,
            plan_id=str(plan_obj.get("id") or "") or None,
            plan_version=plan_obj["version"],
            previous_plan_id=str(plan_obj.get("previous_plan_id") or "") or None,
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


async def _publish_fallback_plan(
    state: SessionState,
    skill_update: dict[str, Any],
    *,
    reason: str,
) -> SessionState:
    plan_obj = _fallback_plan_from_task_frame(
        user_message=last_human_turn_text(state),
        task_frame=state.get("task_frame") if isinstance(state.get("task_frame"), dict) else None,
    )
    previous_plan = _previous_plan_for_current_run(state)
    plan_obj["version"] = _coerce_nonnegative_int((previous_plan or {}).get("version")) + 1
    plan_obj["previous_plan_id"] = (previous_plan or {}).get("id")
    plan_obj["todos"] = [
        todo.model_dump(mode="json")
        for todo in _reconcile_completed_todos(_coerce_todos(plan_obj.get("todos")), previous_plan)
    ]
    log.warning(
        "graph.plan.fallback_from_task_frame",
        session_id=str(state.get("session_id")),
        reason=reason,
        n=len(plan_obj.get("todos") or []),
    )
    return await _publish_plan(state, plan_obj, skill_update)


async def plan_node(state: SessionState) -> SessionState:
    settings = get_settings()
    session_id = state["session_id"]
    run_id = state.get("run_id")
    if state.get("skip_planner"):
        log.info("graph.plan.skipped", session_id=str(session_id), reason="skip_planner")
        return {}

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
        return await _publish_fallback_plan(state, skill_update, reason="template_missing")

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
        return await _publish_fallback_plan(state, skill_update, reason="llm_failed")

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
        return await _publish_fallback_plan(state, skill_update, reason="unparseable")

    if bool(parsed.get("needs_clarification")):
        frame = dict(state.get("task_frame") or {})
        questions = _coerce_clarification_questions(
            parsed.get("clarification_questions") or parsed.get("clarification_question")
        )
        if not questions:
            questions = _coerce_clarification_questions(["请补充执行该计划所需的关键约束。"])
        frame["needs_clarification"] = True
        frame["should_invoke_planner"] = False
        frame["clarification_questions"] = questions
        frame["reasoning_summary"] = "Planner 在拆解步骤时发现仍缺少关键约束"
        await _emit_task_frame_ui(session_id, run_id, frame)
        path = await persist_task_frame_pointer(state, frame)
        return {"task_frame": frame, "task_frame_path": path, **skill_update}

    # Planner output is a proposal, never authoritative execution state.  A
    # model-written `status: done` must not make work appear completed.
    todos = _coerce_todos(parsed.get("todos"), preserve_status=False)
    if not todos:
        log.info("graph.plan.empty", reasoning=parsed.get("reasoning"))
        return await _publish_fallback_plan(state, skill_update, reason="empty_todos")

    previous_plan = _previous_plan_for_current_run(state)
    previous_version = _coerce_nonnegative_int((previous_plan or {}).get("version"))
    coerced = _reconcile_completed_todos(todos, previous_plan)
    plan_id = f"plan_{uuid4().hex[:8]}"
    plan_obj = {
        "id": plan_id,
        "reasoning": parsed.get("reasoning"),
        "todos": [t.model_dump(mode="json") for t in coerced],
        "estimated_steps": parsed.get("estimated_steps"),
        "version": previous_version + 1,
        "previous_plan_id": (previous_plan or {}).get("id"),
    }

    log.info(
        "graph.plan.ready",
        session_id=str(session_id),
        plan_id=plan_id,
        n=len(coerced),
        model=planner_model,
    )
    return await _publish_plan(state, plan_obj, skill_update)


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
                    depends_on=_coerce_str_list(t.get("depends_on"), limit=20, item_limit=80),
                    tool_hint=(str(t.get("tool_hint") or "").strip() or None),
                    acceptance_criteria=_coerce_str_list(t.get("acceptance_criteria"), limit=8),
                    expected_outputs=_coerce_str_list(t.get("expected_outputs"), limit=12),
                    evidence_paths=_coerce_str_list(t.get("evidence_paths"), limit=50, item_limit=500),
                    tool_call_count=max(0, int(t.get("tool_call_count") or 0)),
                    attempts=max(0, int(t.get("attempts") or 0)),
                    completion_reason=(str(t.get("completion_reason") or "").strip() or None),
                    failure_reason=(str(t.get("failure_reason") or "").strip() or None),
                )
            )
        except Exception:  # noqa: BLE001
            continue
    return out


async def _emit_plan_update(*, session_id, run_id, plan: dict[str, Any], todos: list[dict[str, Any]]) -> None:
    try:
        await emit(
            PlanUpdateEvent(
                session_id=session_id,
                run_id=run_id,
                plan_id=str(plan.get("id") or "") or None,
                plan_version=max(1, int(plan.get("version") or 1)),
                previous_plan_id=str(plan.get("previous_plan_id") or "") or None,
                todos=_todos_to_items(todos),
            )
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("graph.plan.progress_emit_failed", error=str(exc))


_VIDEO_EXTS = {".mp4", ".webm", ".mov", ".mkv", ".m4v"}
_AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"}
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".avif", ".svg"}
_CLIP_NAME_PREFIXES = ("wan_t2v_", "wan_i2v_", "wan_r2v_", "wan_video_edit_")
_MEDIA_GEN_TOOLS = frozenset(
    {
        "minimax_tts",
        "wan_text2image",
        "wan_t2v",
        "wan_i2v",
        "wan_r2v",
        "wan_video_edit",
    }
)
_COUNT_RANGE_RE = re.compile(r"(\d+)\s*[-~～到至]\s*(\d+)")
_COUNT_ITEM_RE = re.compile(r"(\d+)\s*(?:条|个|段|份|张)")


def _todo_requires_artifact_proof(todo: dict[str, Any]) -> bool:
    text = str(todo.get("text") or "").lower()
    # 旁白脚本 / 分镜文案是内容步骤，不要求先落盘。
    if any(k in text for k in ("旁白", "分镜", "文案", "时间轴")) and not any(
        k in text for k in ("文件", "html", "网站", "网页")
    ):
        return False
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
    build_artifact_subjects = ("前端", "html", "react", "next.js", "项目", "代码", "应用", "demo")
    if "脚本" in text and not any(k in text for k in ("旁白", "分镜", "文案")):
        build_artifact_subjects = (*build_artifact_subjects, "脚本")
    return any(k in text for k in build_artifact_subjects) and any(k in text for k in build_intent_keywords)


def _todo_intent(text: str) -> str:
    t = (text or "").lower()
    if any(k in t for k in ("验收", "必要时重导出", "再导出")):
        return "verify"
    if "验证" in t and any(k in t for k in ("最终", "成片", "时长", "同步", "比例", "交付", "本地")):
        return "verify"
    if any(k in t for k in ("合成", "拼接", "合并成", "合成为", "叠加音频", "混音", "mux")):
        return "mux"
    if "成片" in t and any(k in t for k in ("生成", "导出", "制作")):
        return "mux"
    if any(k in t for k in ("视频片段", "原创视频", "文生视频")):
        return "video_clips"
    if any(k in t for k in ("视频", "片段", "镜头", "clip")) and any(k in t for k in ("生成", "制作", "出")):
        return "video_clips"
    if any(k in t for k in ("旁白音频", "配音", "语音解说")):
        return "audio"
    if ("音频" in t or "tts" in t) and any(k in t for k in ("生成", "录", "制作")):
        return "audio"
    if "旁白" in t and any(k in t for k in ("生成", "录", "制作")) and "脚本" not in t:
        return "audio"
    if any(k in t for k in ("图像", "图片", "插图", "海报", "配图", "主图", "封面图", "图标")) and any(
        k in t for k in ("生成", "绘制", "制作", "创建", "出")
    ):
        return "image"
    if any(k in t for k in ("调研", "搜索", "检索", "查找资料", "搜集", "收集", "抓取资料")):
        return "research"
    if any(k in t for k in ("安装", "依赖", "ffmpeg", "检查环境", "环境检查")):
        return "setup"
    if _todo_requires_artifact_proof({"text": text}):
        return "artifact"
    return "generic"


def _expected_count(text: str) -> int | None:
    raw = text or ""
    m = _COUNT_RANGE_RE.search(raw)
    if m:
        return max(1, int(m.group(1)))
    m = _COUNT_ITEM_RE.search(raw)
    if m:
        return max(1, int(m.group(1)))
    return None


def _path_ext(path: str) -> str:
    return Path(path).suffix.lower()


def _is_video_path(path: str) -> bool:
    return _path_ext(path) in _VIDEO_EXTS


def _is_audio_path(path: str) -> bool:
    return _path_ext(path) in _AUDIO_EXTS


def _is_image_path(path: str) -> bool:
    return _path_ext(path) in _IMAGE_EXTS


def _is_generated_clip_path(path: str) -> bool:
    return Path(path).name.startswith(_CLIP_NAME_PREFIXES) and _is_video_path(path)


def _turn_tool_names(proof: Any) -> list[str]:
    names = getattr(proof, "tool_names", None)
    collected: list[str] = []
    if isinstance(names, (list, tuple, set)):
        collected = [str(n) for n in names if n]
    if collected:
        return collected
    inferred: list[str] = []
    for path in list(getattr(proof, "written_paths", set()) or set()):
        if not isinstance(path, str):
            continue
        name = Path(path).name
        if name.startswith("wan_t2v_"):
            inferred.append("wan_t2v")
        elif name.startswith("wan_i2v_"):
            inferred.append("wan_i2v")
        elif name.startswith("wan_r2v_"):
            inferred.append("wan_r2v")
        elif name.startswith("wan_video_edit_"):
            inferred.append("wan_video_edit")
        elif name.startswith("minimax_tts_"):
            inferred.append("minimax_tts")
        elif name.startswith("wan_t2i_"):
            inferred.append("wan_text2image")
    return inferred


def _written_paths(proof: Any) -> set[str]:
    raw = getattr(proof, "written_paths", set()) or set()
    return {p for p in raw if isinstance(p, str) and p}


def _proof_operations(proof: Any) -> list[dict[str, Any]]:
    raw = getattr(proof, "operations", None)
    if not isinstance(raw, list):
        return []
    return [dict(item) for item in raw if isinstance(item, dict)]


def _shell_commands(proof: Any) -> list[str]:
    return [
        str((item.get("args") or {}).get("cmd") or "")
        for item in _proof_operations(proof)
        if str(item.get("tool") or "") == "shell" and isinstance(item.get("args"), dict)
    ]


def _todo_paths(todo: dict[str, Any]) -> list[str]:
    return [str(p) for p in (todo.get("evidence_paths") or []) if isinstance(p, str) and p]


def _expected_file_outputs_satisfied(todo: dict[str, Any], paths: set[str]) -> bool:
    """Enforce planner-declared filenames/globs when they look like file outputs."""
    expectations = _coerce_str_list(todo.get("expected_outputs"), limit=12, item_limit=300)
    file_expectations = []
    for item in expectations:
        candidate = item.strip().strip("`\"'")
        suffix = Path(candidate).suffix
        if not any(char.isspace() for char in candidate) and re.fullmatch(r"\.[A-Za-z0-9*?]{1,10}", suffix):
            file_expectations.append(candidate)
    if not file_expectations:
        return True
    normalized_paths = {path.replace("\\", "/") for path in paths}
    for expected in file_expectations:
        pattern = expected.replace("\\", "/")
        if not any(
            candidate == pattern
            or candidate.endswith("/" + pattern)
            or fnmatch(candidate, pattern)
            or fnmatch(Path(candidate).name, Path(pattern).name)
            for candidate in normalized_paths
        ):
            return False
    return True


def _in_progress_todo(todos: list[dict[str, Any]]) -> dict[str, Any] | None:
    for t in todos:
        if t.get("status") == TodoStatus.in_progress:
            return t
    return None


def _dependencies_done(todo: dict[str, Any], todos: list[dict[str, Any]]) -> bool:
    by_id = {str(item.get("id")): item for item in todos}
    for dep_id in _coerce_str_list(todo.get("depends_on"), limit=20, item_limit=80):
        dependency = by_id.get(dep_id)
        if dependency is None or _status_value(dependency.get("status")) != TodoStatus.done.value:
            return False
    return True


def _start_next_pending(todos: list[dict[str, Any]]) -> bool:
    """Start only the first unresolved item and never cross a failed prerequisite."""
    for index, t in enumerate(todos):
        status = _status_value(t.get("status") or TodoStatus.pending)
        if status == TodoStatus.done.value:
            continue
        if status in {TodoStatus.failed.value, TodoStatus.skipped.value, TodoStatus.in_progress.value}:
            return False
        if any(_status_value(prev.get("status")) != TodoStatus.done.value for prev in todos[:index]):
            return False
        if not _dependencies_done(t, todos):
            return False
        if t.get("status") == TodoStatus.pending:
            t["status"] = TodoStatus.in_progress.value
            t["attempts"] = max(0, int(t.get("attempts") or 0)) + 1
            t["failure_reason"] = None
            return True
    return False


def _retry_first_failed(todos: list[dict[str, Any]]) -> bool:
    """Re-open the earliest failed item; dependants remain blocked."""
    for index, todo in enumerate(todos):
        status = _status_value(todo.get("status") or TodoStatus.pending)
        if status == TodoStatus.done.value:
            continue
        if status != TodoStatus.failed.value:
            return False
        if any(_status_value(prev.get("status")) != TodoStatus.done.value for prev in todos[:index]):
            return False
        if not _dependencies_done(todo, todos):
            return False
        todo["status"] = TodoStatus.in_progress.value
        todo["attempts"] = max(0, int(todo.get("attempts") or 0)) + 1
        todo["failure_reason"] = None
        return True
    return False


def _proof_satisfies_todo(todo: dict[str, Any], proof: Any) -> bool:
    """当前 TODO 是否已经有对得上这条文案的证据。一次成功工具不够。"""
    has_success = int(getattr(proof, "successful_tool_calls", 0) or 0) > 0
    if not has_success:
        return False

    intent = _todo_intent(str(todo.get("text") or ""))
    tools = _turn_tool_names(proof)
    new_paths = _written_paths(proof)
    all_paths = set(_todo_paths(todo)) | new_paths
    inspect_only = (set(tools) <= {"shell"} if tools else not new_paths) and not new_paths
    verified_paths = {p for p in (getattr(proof, "verified_paths", set()) or set()) if isinstance(p, str)}
    commands = [command.lower() for command in _shell_commands(proof) if command.strip()]

    if not _expected_file_outputs_satisfied(todo, all_paths):
        return False

    if intent == "setup":
        required_bins = [
            name for name in ("ffmpeg", "ffprobe") if name in str(todo.get("text") or "").lower()
        ]
        if required_bins:
            return bool(commands) and all(
                any(name in command for command in commands) for name in required_bins
            )
        return bool(commands or verified_paths)

    if inspect_only and intent not in {"setup", "verify"}:
        return False

    if intent == "research":
        return "search" in tools or any(_path_ext(p) in {".md", ".txt", ".json"} for p in new_paths)

    if intent == "audio":
        return any(_is_audio_path(p) for p in all_paths)

    if intent == "image":
        images = [p for p in all_paths if _is_image_path(p)]
        need = _expected_count(str(todo.get("text") or "")) or 1
        return len(images) >= need

    if intent == "video_clips":
        videos = [p for p in all_paths if _is_video_path(p)]
        need = _expected_count(str(todo.get("text") or "")) or 1
        return len(videos) >= need

    if intent == "mux":
        if any(t in _MEDIA_GEN_TOOLS for t in tools):
            return False
        return any(_is_video_path(p) and not _is_generated_clip_path(p) for p in new_paths)

    if intent == "verify":
        if any(t in _MEDIA_GEN_TOOLS for t in tools):
            return False
        if any(_is_generated_clip_path(p) for p in new_paths):
            return False
        if verified_paths:
            return True
        verify_markers = (
            "ffprobe",
            "pytest",
            "npm test",
            "npm run test",
            "npm run build",
            "tsc --noemit",
            "curl ",
            " test ",
            "stat ",
        )
        return any(any(marker in f" {command} " for marker in verify_markers) for command in commands)

    if intent == "artifact":
        return bool(new_paths)

    # generic：必须形成文件或完成明确的非搜索、非媒体操作；不能用后续媒体调用
    # 反向推断前面的写稿/分镜步骤已经完成。
    if any(t in _MEDIA_GEN_TOOLS for t in tools):
        return False
    if "search" in tools:
        return False
    if new_paths:
        return True
    return bool(set(tools) - {"shell", "search"} - set(_MEDIA_GEN_TOOLS))


def _proof_is_relevant_to_todo(todo: dict[str, Any], proof: Any) -> bool:
    if int(getattr(proof, "successful_tool_calls", 0) or 0) <= 0:
        return False
    intent = _todo_intent(str(todo.get("text") or ""))
    tools = _turn_tool_names(proof)
    paths = _written_paths(proof)
    if intent == "audio":
        return any(_is_audio_path(path) for path in paths)
    if intent == "image":
        return any(_is_image_path(path) for path in paths) and "wan_text2image" in tools
    if intent == "video_clips":
        return any(_is_video_path(path) for path in paths) and any(tool in _MEDIA_GEN_TOOLS for tool in tools)
    if intent == "mux":
        return not any(tool in _MEDIA_GEN_TOOLS for tool in tools) and any(
            _is_video_path(path) and not _is_generated_clip_path(path) for path in paths
        )
    if intent == "research":
        return "search" in tools or any(_path_ext(path) in {".md", ".txt", ".json"} for path in paths)
    if intent == "setup":
        return bool(_shell_commands(proof) or getattr(proof, "verified_paths", set()))
    if intent == "verify":
        return bool(getattr(proof, "verified_paths", set()) or _shell_commands(proof))
    if intent == "artifact":
        return bool(paths)
    if any(tool in _MEDIA_GEN_TOOLS for tool in tools):
        return False
    return bool(paths or (set(tools) - {"shell", "search"}))


def _record_todo_evidence(todo: dict[str, Any], proof: Any) -> bool:
    changed = False
    existing_paths = [str(p) for p in (todo.get("evidence_paths") or []) if isinstance(p, str)]
    seen = set(existing_paths)
    for path in list(getattr(proof, "written_paths", set()) or set()):
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


def _completion_reason(todo: dict[str, Any], proof: Any) -> str:
    intent = _todo_intent(str(todo.get("text") or ""))
    tools = _turn_tool_names(proof)
    paths = sorted(_written_paths(proof))
    verified = sorted(p for p in (getattr(proof, "verified_paths", set()) or set()) if isinstance(p, str))
    bits: list[str] = []
    if tools:
        bits.append("工具：" + "、".join(dict.fromkeys(tools)))
    if intent == "verify" and verified:
        bits.append("已核验：" + "、".join(verified[:3]))
    elif paths:
        bits.append("产物：" + "、".join(paths[:3]))
    return "；".join(bits) or "已获得与本步骤匹配的执行证据"


async def advance_with_proof(
    plan: dict[str, Any] | None,
    *,
    session_id,
    run_id,
    proof: Any,
) -> dict[str, Any] | None:
    """用本轮执行证据更新当前 TODO，证据对得上这条文案才勾完。

    shell 检查环境不会推进计划。出片类 TODO 要凑够视频文件；合成 / 验收
    不会被下一条素材生成一并勾掉。每轮证据最多只能完成当前一步。
    """
    todos = _plan_todos(plan)
    if not todos:
        return plan

    current = _in_progress_todo(todos)
    if current is None:
        return plan

    changed = False
    relevant = _proof_is_relevant_to_todo(current, proof)
    if relevant and _record_todo_evidence(current, proof):
        changed = True
    if relevant and _proof_satisfies_todo(current, proof):
        current["status"] = TodoStatus.done.value
        current["completion_reason"] = _completion_reason(current, proof)
        current["failure_reason"] = None
        changed = True
        _start_next_pending(todos)

    if not changed:
        return plan

    new_plan = _rebuild_plan(plan, todos)
    await _emit_plan_update(session_id=session_id, run_id=run_id, plan=new_plan, todos=todos)
    return new_plan


_TEXT_DELIVERABLE_TODO_RE = re.compile(
    r"(脚本|文案|内容|答案|回答|答复|总结|整理|分析|结论|时间轴|分镜)",
    re.IGNORECASE,
)
_TEXT_PROMISE_RE = re.compile(r"(我将|接下来|稍后|准备).{0,20}(生成|撰写|制作|整理|分析)")


async def advance_with_response_text(
    plan: dict[str, Any] | None,
    *,
    session_id,
    run_id,
    response_text: str,
) -> dict[str, Any] | None:
    """Complete one text-content TODO when the response itself is the evidence."""
    todos = _plan_todos(plan)
    current = _in_progress_todo(todos)
    text = (response_text or "").strip()
    if not current or len(text) < 20:
        return plan
    todo_text = str(current.get("text") or "")
    if (
        _todo_intent(todo_text) != "generic"
        or _todo_requires_artifact_proof(current)
        or not _TEXT_DELIVERABLE_TODO_RE.search(todo_text)
        or _TEXT_PROMISE_RE.search(text[:160])
        or not _expected_file_outputs_satisfied(current, set())
    ):
        return plan

    current["status"] = TodoStatus.done.value
    current["completion_reason"] = f"已在当前回复中产出 {len(text)} 字文本内容"
    current["failure_reason"] = None
    _start_next_pending(todos)
    new_plan = _rebuild_plan(plan, todos)
    await _emit_plan_update(session_id=session_id, run_id=run_id, plan=new_plan, todos=todos)
    return new_plan


async def mark_progress(
    plan: dict[str, Any] | None,
    *,
    session_id,
    run_id,
    complete_current: bool = False,
    start_next: bool = False,
    fail_current: bool = False,
    retry_failed: bool = False,
    close_unfinished: bool = False,
    failure_reason: str | None = None,
) -> dict[str, Any] | None:
    """Mutate the plan's todo statuses and emit a PlanUpdateEvent.

    - `complete_current`: mark the first in_progress item as `done`.
    - `fail_current`: mark the first in_progress item as `failed`.
    - `start_next`: mark the first `pending` item as `in_progress`.
    - `retry_failed`: re-open the earliest failed item instead of crossing it.
    - `close_unfinished`: mark `in_progress` as `failed` and `pending` as `skipped`.
      Use only when the run must stop without proving the remaining work.
    Returns the new plan dict (or the original if nothing changed / no plan).
    """
    todos = _plan_todos(plan)
    if not todos:
        return plan

    changed = False

    if close_unfinished:
        for t in todos:
            if t.get("status") == TodoStatus.in_progress:
                t["status"] = TodoStatus.failed.value
                t["failure_reason"] = failure_reason or "本轮结束时该步骤尚未完成"
                changed = True
            elif t.get("status") == TodoStatus.pending:
                t["status"] = TodoStatus.skipped.value
                t["failure_reason"] = failure_reason or "前序步骤未完成，本轮未执行该步骤"
                changed = True

    if (complete_current or fail_current) and any(t.get("status") == TodoStatus.in_progress for t in todos):
        target_status = TodoStatus.failed.value if fail_current else TodoStatus.done.value
        for t in todos:
            if t.get("status") == TodoStatus.in_progress:
                t["status"] = target_status
                if fail_current:
                    t["failure_reason"] = failure_reason or "步骤执行失败，等待重试或重规划"
                    t["completion_reason"] = None
                changed = True
                break

    if (
        start_next
        and not any(t.get("status") == TodoStatus.in_progress for t in todos)
        and ((retry_failed and _retry_first_failed(todos)) or _start_next_pending(todos))
    ):
        changed = True

    if not changed:
        return plan

    new_plan = _rebuild_plan(plan, todos)
    await _emit_plan_update(session_id=session_id, run_id=run_id, plan=new_plan, todos=todos)
    return new_plan
