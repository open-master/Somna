"""plan node — produces a TODO list before the executor starts.

Uses the planner prompt template and the `agent-planner` model alias. Failure
is non-fatal: if the planner model errors or returns unusable JSON, we emit a
minimal TODO list derived from the task frame instead of continuing with no plan.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from uuid import uuid4

from langchain_core.messages import SystemMessage
from somna_events import PlanUpdateEvent, SessionPhase, SkillDebugEvent, StatusEvent, TodoItem, TodoStatus

from app.config import get_settings
from app.events.emitter import emit
from app.graph.nodes.task_frame import deliverable_type_implies_artifact, format_task_frame_block
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


def _fallback_plan_from_task_frame(
    *,
    user_message: str,
    task_frame: dict[str, Any] | None,
) -> dict[str, Any]:
    """Minimal TODOs from framing when the planner LLM fails. Not a new planner."""
    tf = task_frame if isinstance(task_frame, dict) else {}
    todos: list[TodoItem] = []
    seen: set[str] = set()

    def _add(text: str) -> None:
        cleaned = " ".join(text.split()).strip()[:300]
        if not cleaned or cleaned in seen or len(todos) >= 4:
            return
        seen.add(cleaned)
        todos.append(
            TodoItem(id=str(len(todos) + 1), text=cleaned, status=TodoStatus.pending)
        )

    criteria = tf.get("success_criteria") if isinstance(tf.get("success_criteria"), list) else []
    for item in criteria:
        _add(str(item).strip())

    goal = (user_message or "").strip()
    if len(goal) > 80:
        goal = goal[:80].rstrip() + "…"
    if not todos:
        _add(f"按定调完成：{goal}" if goal else "完成本轮任务")
        if deliverable_type_implies_artifact(str(tf.get("deliverable_type") or "")):
            _add("生成并验证交付文件")
        else:
            _add("核对结果是否满足任务要求")
    elif deliverable_type_implies_artifact(str(tf.get("deliverable_type") or "")):
        blob = " ".join(t.text for t in todos)
        if not any(k in blob for k in ("文件", "交付", "产物", "artifact")):
            _add("验证交付文件已写入沙盒")

    return {
        "id": f"plan_fallback_{uuid4().hex[:8]}",
        "reasoning": "planner 失败，按任务定调生成最小 TODO",
        "todos": [t.model_dump() for t in todos],
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

    todos = _coerce_todos(parsed.get("todos"))
    if not todos:
        log.info("graph.plan.empty", reasoning=parsed.get("reasoning"))
        return await _publish_fallback_plan(state, skill_update, reason="empty_todos")

    plan_id = f"plan_{uuid4().hex[:8]}"
    plan_obj = {
        "id": plan_id,
        "reasoning": parsed.get("reasoning"),
        "todos": [t.model_dump() for t in todos],
        "estimated_steps": parsed.get("estimated_steps"),
    }

    log.info(
        "graph.plan.ready",
        session_id=str(session_id),
        plan_id=plan_id,
        n=len(todos),
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


_VIDEO_EXTS = {".mp4", ".webm", ".mov", ".mkv", ".m4v"}
_AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"}
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
_MAX_COMPLETIONS_PER_TURN = 2


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
    if any(k in t for k in ("视频", "片段", "镜头", "clip")) and any(
        k in t for k in ("生成", "制作", "出")
    ):
        return "video_clips"
    if any(k in t for k in ("旁白音频", "配音", "语音解说")):
        return "audio"
    if ("音频" in t or "tts" in t) and any(k in t for k in ("生成", "录", "制作")):
        return "audio"
    if "旁白" in t and any(k in t for k in ("生成", "录", "制作")) and "脚本" not in t:
        return "audio"
    if any(k in t for k in ("调研", "搜索", "检索", "查找资料", "搜集")):
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


def _todo_paths(todo: dict[str, Any]) -> list[str]:
    return [str(p) for p in (todo.get("evidence_paths") or []) if isinstance(p, str) and p]


def _in_progress_todo(todos: list[dict[str, Any]]) -> dict[str, Any] | None:
    for t in todos:
        if t.get("status") == TodoStatus.in_progress:
            return t
    return None


def _start_next_pending(todos: list[dict[str, Any]]) -> bool:
    for t in todos:
        if t.get("status") == TodoStatus.pending:
            t["status"] = TodoStatus.in_progress
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

    if intent == "setup":
        return True

    if inspect_only and intent not in {"setup", "verify"}:
        return False

    if intent == "research":
        return "search" in tools or any(_path_ext(p) in {".md", ".txt", ".json"} for p in new_paths)

    if intent == "audio":
        return any(_is_audio_path(p) for p in all_paths)

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
        return "shell" in tools or "filesystem" in tools

    if intent == "artifact":
        return bool(new_paths)

    # generic：shell 探路不完成；search 归调研；媒体生成视为已跳到制作，结束写稿类步骤。
    if any(t in _MEDIA_GEN_TOOLS for t in tools):
        return True
    if "search" in tools:
        return False
    if new_paths:
        return True
    return bool(set(tools) - {"shell", "search"})


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


async def advance_with_proof(
    plan: dict[str, Any] | None,
    *,
    session_id,
    run_id,
    proof: Any,
) -> dict[str, Any] | None:
    """用本轮执行证据更新当前 TODO，证据对得上这条文案才勾完。

    shell 检查环境不会推进计划。出片类 TODO 要凑够视频文件；合成 / 验收
    不会被下一条素材生成一并勾掉。同一轮最多结束写稿 + 配音两步，避免
    一条 wan_t2v 把后面的合成、验收全部烧完。
    """
    todos = _plan_todos(plan)
    if not todos:
        return plan

    current = _in_progress_todo(todos)
    if current is None:
        return plan

    changed = False
    completions = 0
    while completions < _MAX_COMPLETIONS_PER_TURN:
        current = _in_progress_todo(todos)
        if current is None:
            break
        intent = _todo_intent(str(current.get("text") or ""))
        if completions > 0 and intent in {"mux", "verify", "video_clips", "artifact"}:
            break
        if _record_todo_evidence(current, proof):
            changed = True
        if not _proof_satisfies_todo(current, proof):
            break
        current["status"] = TodoStatus.done
        completions += 1
        changed = True
        _start_next_pending(todos)

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
