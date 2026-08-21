"""execute node — streaming tool-calling loop.

Flow per turn:
    1. Build OpenAI chat messages from state + system prompt.
    2. Stream chat.completions with `tools=[...]`.
       - Text deltas → `message.delta` events.
       - Tool call deltas → accumulate `name` and `arguments`.
    3. After stream closes:
       - `finish_reason == "tool_calls"` → for each call: emit `tool.call`,
          invoke via MCP Hub, emit `tool.result`, append tool result message,
          loop again.
       - `finish_reason == "stop"` → append AIMessage, exit loop.
    4. Emit `token.usage` after each completion turn (may use executor vs coder model).

Stops early at `agent_max_turns` to prevent runaway loops.
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import re
import shlex
import time
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote
from uuid import uuid4

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from somna_events import (
    ArtifactEvent,
    MessageDeltaEvent,
    ScreenshotEvent,
    SessionPhase,
    SkillDebugEvent,
    StatusEvent,
    ToolCallEvent,
    ToolResultEvent,
)

from app.config import get_settings
from app.events.emitter import emit
from app.graph.autonomy_policy import delivery_validation_policy, effective_autonomy_level
from app.graph.compact import maybe_compact
from app.graph.model_policy import pick_executor_turn_model
from app.graph.nodes.plan import advance_with_proof, mark_progress
from app.graph.nodes.task_frame import (
    _coerce_clarification_questions,
    _emit_task_frame_ui,
    deliverable_type_implies_artifact,
    format_task_frame_block,
)
from app.graph.run_artifacts import (
    append_executor_progress_snapshot,
    persist_task_frame_pointer,
    sync_plan_artifact,
)
from app.graph.state import SessionState
from app.graph.user_turn import executor_messages_for_current_turn, last_human_turn_text
from app.llm.client import get_async_openai
from app.logging_setup import get_logger
from app.memory import format_memories, search_memories
from app.prompts.loader import build_system_prompt
from app.services.billing import emit_model_usage, record_tool_usage, reserve_tool_points
from app.services.skill_router import (
    SkillRouteResult,
    route_result_from_payload,
    route_skills_for_task,
    selected_skills_payload,
)
from app.tools.client import ToolManifest, ToolResult, get_client
from app.tools.schema import manifests_to_openai_tools, openai_tool_choice, tool_manifest_cache

log = get_logger(__name__)

ASK_USER_TOOL_NAME = "ask_user"
ASK_USER_MANIFEST = ToolManifest(
    name=ASK_USER_TOOL_NAME,
    description=(
        "当必须由用户拍板才能继续时调用：多种实现方案、工具或积分失败后的取舍、"
        "不可逆操作、关键参数缺失。调用后本轮立即暂停并弹出确认卡片。"
        "不要在聊天正文里列出 A/B/C；把问题和选项放进本工具参数。"
        "用户提交后下一轮会带着选择继续执行，不要把选项写进气泡后自己接着跑。"
    ),
    category="hitl",
    mutates=False,
    input_schema={
        "type": "object",
        "required": ["prompt", "options"],
        "properties": {
            "prompt": {
                "type": "string",
                "description": "需要用户确认的问题（一句中文）",
            },
            "options": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 2,
                "maxItems": 5,
                "description": "2～5 个互斥短选项；不要包含「其他」或「由你决定」",
            },
            "allow_custom": {
                "type": "boolean",
                "default": True,
                "description": "是否允许用户自行输入。默认 true",
            },
        },
        "additionalProperties": False,
    },
)

_MCP_TOOLS_OPTIONAL_MODEL = frozenset(
    {
        "visual_critique",
        "wan_text2image",
        "wan_t2v",
        "wan_i2v",
        "wan_r2v",
        "wan_video_edit",
        "minimax_tts",
    }
)


def effective_mcp_tool_models_map(state: SessionState) -> dict[str, str]:
    """Browser/workflow 合并后的 MCP 工具默认 model（用于补全 LLM 未传的 model）。"""
    raw = state.get("mcp_tool_models")
    m: dict[str, str] = {}
    if isinstance(raw, dict):
        for k, v in raw.items():
            if isinstance(v, str) and v.strip():
                m[str(k)] = v.strip()
    return m


@dataclass
class _ExecutionProof:
    successful_tool_calls: int = 0
    mutating_tool_calls: int = 0
    non_search_tool_calls: int = 0
    mock_search_calls: int = 0
    written_paths: set[str] = field(default_factory=set)
    verified_paths: set[str] = field(default_factory=set)
    failed_tool_calls: int = 0
    recovered_failures: int = 0
    failure_notes: list[str] = field(default_factory=list)
    user_questions: list[dict[str, Any]] = field(default_factory=list)
    tool_names: list[str] = field(default_factory=list)


@dataclass
class _ShellRecoveryPlan:
    reason: str
    install_cmd: str


async def _append_executor_progress(
    state: SessionState,
    *,
    sandbox_id: str,
    run_id: str | None,
    tool_turns: int,
    proof: _ExecutionProof,
    note: str,
) -> None:
    if not run_id:
        return
    await append_executor_progress_snapshot(
        state,
        sandbox_id=sandbox_id,
        run_id=run_id,
        tool_turns=tool_turns,
        ok_calls=proof.successful_tool_calls,
        n_written=len(proof.written_paths),
        n_verified=len(proof.verified_paths),
        note=note,
    )


def _merge_proof(base: _ExecutionProof, delta: _ExecutionProof) -> _ExecutionProof:
    base.successful_tool_calls += delta.successful_tool_calls
    base.mutating_tool_calls += delta.mutating_tool_calls
    base.non_search_tool_calls += delta.non_search_tool_calls
    base.mock_search_calls += delta.mock_search_calls
    base.written_paths.update(delta.written_paths)
    base.verified_paths.update(delta.verified_paths)
    base.failed_tool_calls += delta.failed_tool_calls
    base.recovered_failures += delta.recovered_failures
    if delta.failure_notes:
        base.failure_notes.extend(delta.failure_notes)
        if len(base.failure_notes) > 8:
            base.failure_notes = base.failure_notes[-8:]
    if delta.user_questions:
        base.user_questions.extend(delta.user_questions)
        if len(base.user_questions) > 4:
            base.user_questions = base.user_questions[:4]
    if delta.tool_names:
        base.tool_names.extend(delta.tool_names)
    return base


def _compose_executor_extra_context(
    memory_block: str | None,
    task_frame: dict[str, Any] | None,
    skill_block: str | None = None,
    *,
    plan: dict[str, Any] | None = None,
    compact_memory: str | None = None,
) -> str | None:
    parts: list[str] = []
    if memory_block and str(memory_block).strip():
        parts.append(f"### 用户长期记忆（来自 mem0）\n{memory_block.strip()}")
    if skill_block and str(skill_block).strip():
        parts.append(f"### 本轮选中的 Skills（Claude 标准 Skill）\n{skill_block.strip()}")
    block = format_task_frame_block(task_frame).strip()
    if block and block != "(无)":
        parts.append(f"### 任务定调（phase A framing，供对齐范围与交付）\n{block}")
    todos = plan.get("todos") if isinstance(plan, dict) else None
    if isinstance(todos, list) and todos:
        todo_lines: list[str] = []
        for todo in todos[:30]:
            if not isinstance(todo, dict):
                continue
            text = str(todo.get("text") or "").strip()
            if not text:
                continue
            todo_lines.append(
                f"- [{str(todo.get('status') or 'pending')}] {text}"
            )
        if todo_lines:
            parts.append("### 当前执行计划（续跑时必须保持进度）\n" + "\n".join(todo_lines))
    if compact_memory and compact_memory.strip():
        parts.append(
            "### 已压缩的会话执行摘要（续跑上下文）\n"
            + compact_memory.strip()[:12_000]
        )
    parts.append(
        "### 需要用户拍板时\n"
        "遇到多种方案、工具/积分失败后的取舍、或不可逆操作：必须调用 `ask_user`，"
        "把问题和选项放进工具参数。不要在正文里列出 A/B/C 并继续执行。"
        "调用后本轮会暂停；用户在确认卡片中选择后，下一轮带着选择继续。"
    )
    return "\n\n".join(parts) if parts else None


def _with_fresh_system_prompt(working_messages: list, system_prompt: str) -> list:
    """Ensure executor system context is present even when planner added a SystemMessage."""
    kept = [
        m
        for m in working_messages
        if not (
            isinstance(m, SystemMessage)
            and (
                "你是 **Somna**" in _content_str(m.content)
                or "## Skill 与工具的区别" in _content_str(m.content)
            )
        )
    ]
    return [SystemMessage(content=system_prompt)] + kept


async def _emit_skill_debug_event(session_id, run_id: str | None, skill_route: SkillRouteResult) -> None:
    await emit(
        SkillDebugEvent(
            session_id=session_id,
            run_id=run_id,
            candidate_count=skill_route.candidate_count,
            selected_skills=[
                {
                    "id": item.id,
                    "name": item.name,
                    "reason": item.reason,
                    "load_files": item.load_files,
                    "forced": item.forced,
                }
                for item in skill_route.selected
            ],
        )
    )


async def _route_or_reuse_skills(state: SessionState) -> SkillRouteResult:
    if state.get("skill_route_resolved"):
        cached = route_result_from_payload(
            selected_skills=state.get("selected_skills"),
            prompt_block=state.get("skill_prompt_block"),
            candidate_count=state.get("skill_candidate_count"),
        )
        if cached is not None:
            return cached
    return await route_skills_for_task(
        user_id=state.get("user_id"),
        user_message=(state.get("resume_goal") or last_human_turn_text(state)),
        task_frame=state.get("task_frame") if isinstance(state.get("task_frame"), dict) else None,
        plan=state.get("plan") if isinstance(state.get("plan"), dict) else None,
        skill_mode=state.get("skill_mode"),
        skill_model=state.get("skill_model"),
        session_id=state.get("session_id") if state.get("user_id") else None,
        run_id=state.get("run_id"),
        usage_key=f"{state.get('run_id')}:skill_router:execute",
    )


def _goal_requires_real_artifact(
    user_message: str,
    plan: dict[str, Any] | None,
    task_frame: dict[str, Any] | None = None,
) -> bool:
    if task_frame and isinstance(task_frame, dict):
        if deliverable_type_implies_artifact(str(task_frame.get("deliverable_type") or "")):
            return True
    text = f"{user_message}\n" + "\n".join(
        str(t.get("text") or "") for t in (plan or {}).get("todos", []) if isinstance(t, dict)
    )
    text = text.lower()
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
    if any(k in text for k in build_artifact_subjects) and any(k in text for k in build_intent_keywords):
        return True

    return False


def _missing_delivery_reason(
    *,
    user_message: str,
    plan: dict[str, Any] | None,
    proof: _ExecutionProof,
    task_frame: dict[str, Any] | None = None,
) -> str | None:
    plan_todos = (plan or {}).get("todos", []) if isinstance(plan, dict) else []
    requires_artifact = _goal_requires_real_artifact(user_message, plan, task_frame)

    if proof.mock_search_calls and proof.successful_tool_calls == proof.mock_search_calls:
        return "当前只有 mock 搜索结果，没有真实外部信息或产物"

    if requires_artifact:
        if proof.successful_tool_calls == 0:
            return "任务要求交付网站/代码/文件，但没有任何成功的工具执行"
        if not _artifact_paths(proof):
            return "任务要求交付真实产物，但没有检测到写文件/修改沙盒的证据"

    if len(plan_todos) >= 3 and proof.successful_tool_calls == 0:
        return "存在多步计划，但模型没有实际调用工具就试图结束"

    return None


def _has_execution_progress(proof: _ExecutionProof) -> bool:
    """True when this run already produced tool activity worth reflecting on."""
    return (
        int(proof.successful_tool_calls or 0) > 0
        or int(proof.failed_tool_calls or 0) > 0
        or bool(proof.written_paths)
    )


def _unrecovered_failure_count(proof: _ExecutionProof) -> int:
    return max(0, int(proof.failed_tool_calls) - int(proof.recovered_failures))


def _failure_note(result) -> str:
    output = result.output if isinstance(result.output, dict) else {}
    parts: list[str] = []
    if result.error:
        parts.append(str(result.error).strip()[:160])
    if output.get("timed_out") is True:
        parts.append("timeout")
    rc = output.get("exit_code")
    if isinstance(rc, int) and rc != 0:
        parts.append(f"exit_code={rc}")
    preview = str(result.preview or "").strip()[:80]
    if preview and preview not in (parts[0] if parts else ""):
        parts.append(preview)
    return "; ".join(x for x in parts if x) or "tool failed"


def _unrecovered_failure_reason(proof: _ExecutionProof) -> str | None:
    n = _unrecovered_failure_count(proof)
    if n <= 0:
        return None
    last = (proof.failure_notes[-1] if proof.failure_notes else "工具失败").strip()
    return f"有 {n} 次工具失败尚未恢复（最近：{last[:80]}），不能当作已完成"


def _is_retryable_shell_failure(*, tool_name: str, result) -> bool:
    if tool_name != "shell" or result.ok:
        return False
    err = str(result.error or "")
    if "积分不足" in err or "计费状态已关闭" in err:
        return False
    output = result.output if isinstance(result.output, dict) else {}
    if output.get("timed_out") is True:
        return True
    blob = f"{err} {result.preview or ''}".lower()
    if "timeout" in blob or "timed out" in blob:
        return True
    rc = output.get("exit_code")
    return isinstance(rc, int) and rc != 0


_MIN_DELIVERABLE_BYTES = {
    ".png": 64,
    ".jpg": 64,
    ".jpeg": 64,
    ".gif": 64,
    ".webp": 64,
    ".svg": 24,
    ".pdf": 80,
    ".pptx": 80,
    ".ppt": 80,
    ".mp4": 80,
    ".webm": 80,
    ".mp3": 32,
    ".wav": 32,
    ".html": 24,
    ".htm": 24,
    ".md": 16,
    ".markdown": 16,
    ".json": 8,
    ".csv": 8,
    ".txt": 8,
}


def _min_bytes_for_path(path: str) -> int:
    suffix = Path(str(path)).suffix.lower()
    return int(_MIN_DELIVERABLE_BYTES.get(suffix, 1))


def _thin_written_file_reason(
    *,
    path: str,
    exists: bool,
    is_file: bool,
    size: Any,
) -> str | None:
    if not exists:
        return f"声称已写入 {path}，但文件不存在或不可读"
    if not is_file:
        return None
    try:
        nbytes = int(size) if size is not None else 0
    except (TypeError, ValueError):
        nbytes = 0
    min_b = _min_bytes_for_path(path)
    if nbytes < min_b:
        return f"交付文件过小或为空：{path}（{nbytes} 字节）"
    return None


def _completion_retry_message(reason: str) -> str:
    return (
        "系统校验未通过："
        f"{reason}。\n"
        "不要只口头宣称已完成。请继续调用工具，在沙盒里生成/修改真实文件或执行真实步骤，"
        "并用 filesystem list/stat/read 或 shell 输出验证结果；确认后再结束。"
    )


def _delivery_recovery_tool_name(manifests: list[Any]) -> str | None:
    names = {getattr(m, "name", None) for m in manifests}
    if "shell" in names:
        return "shell"
    if "filesystem" in names:
        return "filesystem"
    return None


def _with_ask_user_manifest(manifests: list[Any]) -> list[Any]:
    out = [m for m in manifests if getattr(m, "name", None) != ASK_USER_TOOL_NAME]
    out.append(ASK_USER_MANIFEST)
    return out


def _parse_ask_user_questions(args: dict[str, Any]) -> list[dict[str, Any]]:
    raw = {
        "id": str(args.get("id") or "q1").strip() or "q1",
        "prompt": str(args.get("prompt") or args.get("question") or "").strip(),
        "options": args.get("options") or [],
        "allow_custom": True,
    }
    questions = _coerce_clarification_questions([raw])
    extra = args.get("questions")
    if extra:
        questions.extend(_coerce_clarification_questions(extra))
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for index, question in enumerate(questions, start=1):
        qid = str(question.get("id") or f"q{index}").strip() or f"q{index}"
        if qid in seen:
            qid = f"q{index}"
        seen.add(qid)
        out.append({**question, "id": qid, "allow_custom": True})
    if not out:
        out = _coerce_clarification_questions(["请确认下一步如何继续。"])
        for question in out:
            question["allow_custom"] = True
    return out[:4]


async def _pause_for_user_decision(
    state: SessionState,
    *,
    questions: list[dict[str, Any]],
    proof: _ExecutionProof,
    plan: dict[str, Any] | None,
    working_messages: list,
    compact_memory: str | None,
    tool_turns: int,
    total_agent_turns: int,
    total_execution_tokens: int,
    turn_text: str,
) -> dict[str, Any]:
    session_id = state["session_id"]
    run_id = state.get("run_id")
    frame = dict(state.get("task_frame") or {})
    frame["needs_clarification"] = True
    frame["awaiting_execute_decision"] = True
    frame["clarification_questions"] = questions
    goal = str(frame.get("execute_resume_goal") or "").strip() or last_human_turn_text(state)
    frame["execute_resume_goal"] = goal[:2000]
    await persist_task_frame_pointer(state, frame)
    await _emit_task_frame_ui(session_id, run_id, frame)
    await emit(
        StatusEvent(
            session_id=session_id,
            run_id=run_id,
            phase=SessionPhase.waiting_user,
            message="等待您确认后再继续执行",
        )
    )
    await _append_executor_progress(
        state,
        sandbox_id=state.get("sandbox_id") or str(session_id),
        run_id=run_id,
        tool_turns=tool_turns,
        proof=proof,
        note="waiting_user",
    )
    log.info(
        "graph.execute.waiting_user",
        session_id=str(session_id),
        run_id=run_id,
        n_questions=len(questions),
    )
    return {
        "assistant_text": turn_text or "",
        "messages": [m for m in working_messages if not isinstance(m, SystemMessage)],
        "compact_memory": compact_memory,
        "tool_turns": tool_turns,
        "total_agent_turns": total_agent_turns,
        "total_execution_tokens": total_execution_tokens,
        "plan": plan,
        "task_frame": frame,
        "execution_summary": _summarize_execution(proof),
        "finished": True,
    }


def _artifact_paths(proof: _ExecutionProof) -> set[str]:
    # Reading/stat-ing a pre-existing file proves only that it exists, not that
    # this run produced the requested deliverable.
    return set(proof.written_paths)


def _summarize_execution(
    proof: _ExecutionProof,
    *,
    delivery_missing_reason: str | None = None,
    execute_exception: str | None = None,
) -> dict[str, Any]:
    return {
        "successful_tool_calls": proof.successful_tool_calls,
        "mutating_tool_calls": proof.mutating_tool_calls,
        "non_search_tool_calls": proof.non_search_tool_calls,
        "mock_search_calls": proof.mock_search_calls,
        "written_paths": sorted(proof.written_paths),
        "verified_paths": sorted(proof.verified_paths),
        "failed_tool_calls": proof.failed_tool_calls,
        "recovered_failures": proof.recovered_failures,
        "unrecovered_failures": _unrecovered_failure_count(proof),
        "failure_notes": list(proof.failure_notes[-8:]),
        "delivery_missing_reason": delivery_missing_reason,
        "execute_exception": execute_exception,
    }


def _proof_from_execution_summary(summary: dict[str, Any] | None) -> _ExecutionProof:
    """Rehydrate delivery-proof counters after `reflect` → execute (same graph run).

    Without this, each execute pass starts with an empty proof while `messages` still
    contain prior ToolMessages, so `_missing_delivery_reason` can false-positive on the
    first text-only model turn of the new pass.
    """
    if not isinstance(summary, dict) or not summary:
        return _ExecutionProof()
    p = _ExecutionProof()
    p.successful_tool_calls = int(summary.get("successful_tool_calls") or 0)
    p.mutating_tool_calls = int(summary.get("mutating_tool_calls") or 0)
    p.non_search_tool_calls = int(summary.get("non_search_tool_calls") or 0)
    p.mock_search_calls = int(summary.get("mock_search_calls") or 0)
    for path in summary.get("written_paths") or []:
        if isinstance(path, str) and path.strip():
            p.written_paths.add(path.strip())
    for path in summary.get("verified_paths") or []:
        if isinstance(path, str) and path.strip():
            p.verified_paths.add(path.strip())
    p.failed_tool_calls = int(summary.get("failed_tool_calls") or 0)
    p.recovered_failures = int(summary.get("recovered_failures") or 0)
    for note in summary.get("failure_notes") or []:
        if isinstance(note, str) and note.strip():
            p.failure_notes.append(note.strip()[:160])
    if len(p.failure_notes) > 8:
        p.failure_notes = p.failure_notes[-8:]
    return p


_PYTHON_MODULE_RE = re.compile(r"No module named ['\"]([^'\"]+)['\"]")
_NODE_MODULE_RE = re.compile(r"Cannot find module ['\"]([^'\"]+)['\"]")
_MISSING_BIN_RE = re.compile(
    r"(?:^|[\n\r])(?:/bin/)?(?:sh|bash):(?: \d+:)? ([a-zA-Z0-9@._/-]+): (?:not found|command not found)",
    re.IGNORECASE,
)
_PYTHON_PACKAGE_MAP = {
    "yaml": "pyyaml",
    "PIL": "pillow",
    "cv2": "opencv-python-headless",
    "bs4": "beautifulsoup4",
    "sklearn": "scikit-learn",
    "dateutil": "python-dateutil",
}


def _normalize_node_package_name(module_name: str) -> str:
    if not module_name:
        return module_name
    if module_name.startswith("@"):
        parts = module_name.split("/")
        return "/".join(parts[:2]) if len(parts) >= 2 else module_name
    return module_name.split("/")[0]


# ------------------------------------------------------------------
#  Accumulator for streamed tool calls
# ------------------------------------------------------------------

class _PendingToolCall:
    __slots__ = ("id", "name", "args_buf")

    def __init__(self) -> None:
        self.id: str = ""
        self.name: str = ""
        self.args_buf: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": "function",
            "function": {"name": self.name, "arguments": self.args_buf or "{}"},
        }


def _parse_args(buf: str) -> dict[str, Any]:
    if not buf:
        return {}
    try:
        return json.loads(buf)
    except json.JSONDecodeError:
        return {}


# ------------------------------------------------------------------
#  Message serialization
# ------------------------------------------------------------------


def _tool_calls_to_openai_wire(raw: Any) -> list[dict[str, Any]] | None:
    """Convert LangChain tool_calls to OpenAI chat.completions wire format.

    LangChain stores items like ``{type: tool_call, id, name, args: dict}``; the
    gateway (Kimi / LiteLLM) expects ``{type: function, function: {name, arguments: str}}``.
    Passing the wrong shape causes the next request to fail with *tool_call_id is not found*.
    """
    if not raw:
        return None
    out: list[dict[str, Any]] = []
    for c in raw:
        if not isinstance(c, dict):
            continue
        if c.get("type") == "function" and isinstance(c.get("function"), dict):
            fn = c["function"]
            tid = str(c.get("id") or "").strip() or f"call_{uuid4().hex[:12]}"
            args_val = fn.get("arguments", "{}")
            if isinstance(args_val, dict):
                arg_str = json.dumps(args_val, ensure_ascii=False)
            else:
                arg_str = str(args_val) if args_val is not None else "{}"
            out.append(
                {
                    "id": tid,
                    "type": "function",
                    "function": {
                        "name": str(fn.get("name") or ""),
                        "arguments": arg_str if arg_str.strip() else "{}",
                    },
                }
            )
            continue
        if "name" in c:
            tid = str(c.get("id") or "").strip() or f"call_{uuid4().hex[:12]}"
            args = c.get("args", {})
            if isinstance(args, dict):
                arg_str = json.dumps(args, ensure_ascii=False)
            else:
                arg_str = str(args) if args is not None else "{}"
            out.append(
                {
                    "id": tid,
                    "type": "function",
                    "function": {"name": str(c.get("name") or ""), "arguments": arg_str or "{}"},
                }
            )
    return out or None


def _langchain_to_openai(messages: list) -> list[dict]:
    out: list[dict] = []
    for m in messages:
        if isinstance(m, SystemMessage):
            out.append({"role": "system", "content": _content_str(m.content)})
        elif isinstance(m, HumanMessage):
            out.append({"role": "user", "content": _content_str(m.content)})
        elif isinstance(m, ToolMessage):
            tid = str(m.tool_call_id or "").strip() or f"call_{uuid4().hex[:12]}"
            row: dict[str, Any] = {
                "role": "tool",
                "tool_call_id": tid,
                "content": _content_str(m.content),
            }
            name = getattr(m, "name", None)
            if name:
                row["name"] = name
            out.append(row)
        elif isinstance(m, AIMessage):
            tc_raw = getattr(m, "tool_calls", None) or m.additional_kwargs.get("tool_calls")
            tc_wire = _tool_calls_to_openai_wire(tc_raw)
            msg: dict[str, Any] = {"role": "assistant", "content": _content_str(m.content)}
            if tc_wire:
                msg["tool_calls"] = tc_wire
            out.append(msg)
        else:
            out.append({"role": "user", "content": _content_str(getattr(m, "content", ""))})
    return out


def _content_str(content: Any) -> str:
    if isinstance(content, str):
        return content
    if content is None:
        return ""
    return str(content)


# ------------------------------------------------------------------
#  Main node
# ------------------------------------------------------------------


async def execute_node(state: SessionState) -> SessionState:
    engine = (state.get("executor_engine") or "native").lower()
    # 模式二历史取值 anthropic / mode2：固定走 Claude Agent SDK，不再维护独立 Messages loop。
    if engine in ("anthropic", "anthropic_compat", "mode2"):
        from app.graph.nodes.execute_agent_sdk import execute_agent_sdk_node

        return await execute_agent_sdk_node(state)

    settings = get_settings()
    session_id = state["session_id"]
    run_id = state.get("run_id")
    exec_alias = (state.get("executor_model") or settings.agent_default_executor).strip()
    coder_alias = (state.get("coder_model") or settings.agent_default_coder).strip()
    sandbox_id = state.get("sandbox_id") or str(session_id)
    mcp_tool_models_map = effective_mcp_tool_models_map(state)

    client = get_async_openai()
    manifests = _with_ask_user_manifest(list(tool_manifest_cache().values()))
    manifest_by_name = {m.name: m for m in manifests}
    tools_schema = manifests_to_openai_tools(manifests)

    # Pull relevant long-term memories so the executor prompt starts with
    # whatever we already know about this user. No-op when memory is off.
    user_message = str(state.get("resume_goal") or "").strip() or last_human_turn_text(state)
    memories = await search_memories(
        user_message,
        session_id=str(session_id),
        user_id=state.get("user_id"),
    )
    memory_block = format_memories(memories)
    skill_route = await _route_or_reuse_skills(state)
    state["selected_skills"] = selected_skills_payload(skill_route)
    state["skill_prompt_block"] = skill_route.prompt_block
    state["skill_candidate_count"] = skill_route.candidate_count
    state["skill_route_resolved"] = True
    await _emit_skill_debug_event(session_id, run_id, skill_route)
    skill_block = skill_route.prompt_block
    extra_context = _compose_executor_extra_context(
        memory_block,
        state.get("task_frame"),
        skill_block,
        plan=state.get("plan"),
        compact_memory=state.get("compact_memory"),
    )
    if state.get("resume_execute"):
        resume_note = (
            "### 用户刚在确认卡片中作出选择\n"
            "请按最新用户消息中的选择继续执行，不要重新从零开始，也不要再问一遍相同的问题。"
        )
        extra_context = f"{extra_context}\n\n{resume_note}" if extra_context else resume_note

    system_prompt = build_system_prompt(
        session_id=str(session_id),
        user_id=state.get("user_id"),
        manifests=manifests,
        extra_context=extra_context,
        enabled_skill_names=[item.name for item in skill_route.selected],
    )

    # Compose initial messages: system + history.
    working_messages = executor_messages_for_current_turn(state)
    working_messages = _with_fresh_system_prompt(working_messages, system_prompt)

    _tf = state.get("task_frame") if isinstance(state.get("task_frame"), dict) else None
    _eff_auto = effective_autonomy_level(_tf)
    _dv_policy = delivery_validation_policy(_eff_auto)
    log.info(
        "graph.execute.start",
        session_id=str(session_id),
        executor_model=exec_alias,
        coder_model=coder_alias,
        skill_model=(state.get("skill_model") or settings.agent_default_skill).strip(),
        selected_skills=state.get("selected_skills") or [],
        n_msgs=len(working_messages),
        tools=len(tools_schema or []),
        effective_autonomy=_eff_auto,
        max_native_delivery_rounds=_dv_policy.max_native_stop_without_delivery,
    )

    prompt_tokens_total = completion_tokens_total = 0
    tool_turns = int(state.get("tool_turns") or 0)
    total_agent_turns = int(state.get("total_agent_turns") or 0)
    total_execution_tokens = int(state.get("total_execution_tokens") or 0)
    max_turns = max(1, int(settings.agent_max_turns))
    max_total_turns = max(1, int(getattr(settings, "agent_max_total_turns", 80)))
    max_total_tokens = max(1, int(getattr(settings, "agent_max_total_tokens", 500000)))
    final_text = ""
    compact_memory = state.get("compact_memory")
    plan = state.get("plan")
    proof = _proof_from_execution_summary(state.get("execution_summary"))
    finish_validation_failures = 0
    forced_tool_name: str | None = None

    # Mark the first TODO as in_progress up front so the UI timeline moves.
    plan = await mark_progress(
        plan, session_id=session_id, run_id=run_id, start_next=True
    )
    await sync_plan_artifact(state, plan)

    try:
        while (
            tool_turns < max_turns
            and total_agent_turns < max_total_turns
            and total_execution_tokens < max_total_tokens
        ):
            # Compact older history if the context is getting heavy.
            working_messages, did_compact, summary = await maybe_compact(
                working_messages,
                session_id=session_id,
                run_id=run_id,
                compact_model=state.get("compact_model"),
                longctx_model=state.get("longctx_model"),
                billing_enabled=bool(state.get("user_id")),
            )
            if did_compact and summary:
                compact_memory = summary
            turn_model = pick_executor_turn_model(
                working_messages=working_messages,
                manifest_by_name=manifest_by_name,
                executor_model=exec_alias,
                coder_model=coder_alias,
            )
            turn_text, pending_calls, usage = await _stream_one_turn(
                client=client,
                model=turn_model,
                messages=_langchain_to_openai(working_messages),
                tools_schema=tools_schema,
                session_id=session_id,
                run_id=run_id,
                forced_tool_name=forced_tool_name,
            )
            total_agent_turns += 1
            total_execution_tokens += max(0, usage[0]) + max(0, usage[1])
            prompt_tokens_total += usage[0]
            completion_tokens_total += usage[1]
            if usage[0] or usage[1]:
                await emit_model_usage(
                    session_id=session_id,
                    run_id=run_id,
                    usage_key=(
                        f"{run_id}:execute:{int(state.get('reflection_count') or 0)}:"
                        f"{tool_turns}:{finish_validation_failures}"
                    ),
                    phase="execute",
                    model=turn_model,
                    input_tokens=usage[0],
                    output_tokens=usage[1],
                )

            # Append assistant turn to conversation.
            assistant_kwargs = {"content": turn_text}
            if pending_calls:
                assistant_kwargs["additional_kwargs"] = {
                    "tool_calls": [pc.to_dict() for pc in pending_calls]
                }
            working_messages.append(AIMessage(**assistant_kwargs))

            if not pending_calls:
                reason = await _stop_blocked_reason(
                    user_message=user_message,
                    plan=plan,
                    proof=proof,
                    task_frame=state.get("task_frame"),
                    sandbox_id=sandbox_id,
                )
                if reason:
                    finish_validation_failures += 1
                    log.warning(
                        "graph.execute.delivery_proof_missing",
                        session_id=str(session_id),
                        run_id=run_id,
                        attempt=finish_validation_failures,
                        reason=reason,
                    )
                    if finish_validation_failures >= _dv_policy.max_native_stop_without_delivery:
                        plan = await mark_progress(
                            plan, session_id=session_id, run_id=run_id, fail_current=True
                        )
                        await sync_plan_artifact(state, plan)
                        await _append_executor_progress(
                            state,
                            sandbox_id=sandbox_id,
                            run_id=run_id,
                            tool_turns=tool_turns,
                            proof=proof,
                            note=f"delivery_validation_failed:{reason[:80]}",
                        )
                        return {
                            "assistant_text": turn_text,
                            "messages": [m for m in working_messages if not isinstance(m, SystemMessage)],
                            "compact_memory": compact_memory,
                            "tool_turns": tool_turns,
                            "total_agent_turns": total_agent_turns,
                            "total_execution_tokens": total_execution_tokens,
                            "plan": plan,
                            "execution_summary": _summarize_execution(
                                proof, delivery_missing_reason=reason
                            ),
                            "finished": True,
                        }
                    forced_tool_name = _delivery_recovery_tool_name(manifests)
                    working_messages.append(SystemMessage(content=_completion_retry_message(reason)))
                    continue
                forced_tool_name = None
                final_text = turn_text
                await _append_executor_progress(
                    state,
                    sandbox_id=sandbox_id,
                    run_id=run_id,
                    tool_turns=tool_turns,
                    proof=proof,
                    note="model_stop",
                )
                break

            tool_turns += 1
            turn_proof = await _run_tool_calls(
                pending_calls,
                sandbox_id=sandbox_id,
                session_id=session_id,
                run_id=run_id,
                working_messages=working_messages,
                manifests=manifests,
                mcp_tool_models=mcp_tool_models_map,
                billing_enabled=bool(state.get("user_id")),
                operation_scope=f"{int(state.get('reflection_count') or 0)}:{tool_turns}",
            )
            proof = _merge_proof(proof, turn_proof)
            finish_validation_failures = 0
            forced_tool_name = None
            plan = await advance_with_proof(
                plan,
                session_id=session_id,
                run_id=run_id,
                proof=turn_proof,
            )
            await sync_plan_artifact(state, plan)
            await _append_executor_progress(
                state,
                sandbox_id=sandbox_id,
                run_id=run_id,
                tool_turns=tool_turns,
                proof=proof,
                note="after_tools",
            )
            if turn_proof.user_questions:
                return await _pause_for_user_decision(
                    state,
                    questions=turn_proof.user_questions,
                    proof=proof,
                    plan=plan,
                    working_messages=working_messages,
                    compact_memory=compact_memory,
                    tool_turns=tool_turns,
                    total_agent_turns=total_agent_turns,
                    total_execution_tokens=total_execution_tokens,
                    turn_text=turn_text,
                )
        else:
            log.warning("graph.execute.max_turns", session_id=str(session_id), turns=tool_turns)
            final_text = (
                "（已达到本轮全局执行预算上限，未能完成任务。请尝试拆小或直接提问。）"
            )
            plan = await mark_progress(
                plan, session_id=session_id, run_id=run_id, fail_current=True
            )
            await sync_plan_artifact(state, plan)
            await _append_executor_progress(
                state,
                sandbox_id=sandbox_id,
                run_id=run_id,
                tool_turns=tool_turns,
                proof=proof,
                note="max_tool_turns",
            )
    except Exception as exc:  # noqa: BLE001
        log.exception("graph.execute.failed", error=str(exc))
        recoverable = _has_execution_progress(proof)
        if not recoverable:
            plan = await mark_progress(
                plan, session_id=session_id, run_id=run_id, fail_current=True
            )
            await sync_plan_artifact(state, plan)
        await _append_executor_progress(
            state,
            sandbox_id=sandbox_id,
            run_id=run_id,
            tool_turns=tool_turns,
            proof=proof,
            note=f"exception:{str(exc)[:120]}",
        )
        payload: dict[str, Any] = {
            "finished": True,
            "assistant_text": final_text,
            "messages": [
                message
                for message in working_messages
                if not isinstance(message, SystemMessage)
            ],
            "compact_memory": compact_memory,
            "tool_turns": tool_turns,
            "total_agent_turns": total_agent_turns,
            "total_execution_tokens": total_execution_tokens,
            "plan": plan,
            "execution_summary": _summarize_execution(
                proof, execute_exception=str(exc)[:240]
            ),
        }
        if recoverable:
            log.warning(
                "graph.execute.failed_recoverable",
                session_id=str(session_id),
                run_id=run_id,
                error=str(exc)[:160],
            )
        else:
            payload["error"] = str(exc)
        return payload

    log.info(
        "graph.execute.done",
        session_id=str(session_id),
        turns=tool_turns,
        chars=len(final_text),
        in_tokens=prompt_tokens_total,
        out_tokens=completion_tokens_total,
    )

    await _append_executor_progress(
        state,
        sandbox_id=sandbox_id,
        run_id=run_id,
        tool_turns=tool_turns,
        proof=proof,
        note="execute_done",
    )

    # Drop the injected SystemMessage if we added one (it's rebuilt each run).
    new_messages = [m for m in working_messages if not isinstance(m, SystemMessage)]

    return {
        "assistant_text": final_text,
        "messages": new_messages,
        "compact_memory": compact_memory,
        "tool_turns": tool_turns,
        "total_agent_turns": total_agent_turns,
        "total_execution_tokens": total_execution_tokens,
        "plan": plan,
        "execution_summary": _summarize_execution(proof),
        "finished": True,
    }


# ------------------------------------------------------------------
#  One LLM turn (streaming)
# ------------------------------------------------------------------


async def _stream_one_turn(
    *,
    client,
    model: str,
    messages: list[dict],
    tools_schema: list[dict] | None,
    session_id,
    run_id,
    forced_tool_name: str | None = None,
) -> tuple[str, list[_PendingToolCall], tuple[int, int]]:
    text_buf = ""
    prompt_tokens = completion_tokens = 0
    tool_calls_by_index: dict[int, _PendingToolCall] = {}

    kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": True,
        "stream_options": {"include_usage": True},
        "temperature": 0.3,
    }
    if tools_schema:
        kwargs["tools"] = tools_schema
        kwargs["tool_choice"] = openai_tool_choice(forced_tool_name)

    stream = await client.chat.completions.create(**kwargs)
    async for chunk in stream:
        if chunk.choices:
            choice = chunk.choices[0]
            delta = choice.delta
            if delta and delta.content:
                text_buf += delta.content
                await emit(
                    MessageDeltaEvent(
                        session_id=session_id,
                        run_id=run_id,
                        text=delta.content,
                    )
                )
            if delta and getattr(delta, "tool_calls", None):
                for tc in delta.tool_calls:
                    idx = tc.index if tc.index is not None else 0
                    pc = tool_calls_by_index.setdefault(idx, _PendingToolCall())
                    if tc.id:
                        pc.id = tc.id
                    fn = getattr(tc, "function", None)
                    if fn:
                        if fn.name:
                            pc.name = fn.name
                        if fn.arguments:
                            pc.args_buf += fn.arguments
        if getattr(chunk, "usage", None):
            prompt_tokens = chunk.usage.prompt_tokens or 0
            completion_tokens = chunk.usage.completion_tokens or 0

    # Ensure each pending call has an id (some providers omit for single call).
    pending: list[_PendingToolCall] = []
    for idx in sorted(tool_calls_by_index):
        pc = tool_calls_by_index[idx]
        if not pc.id:
            pc.id = f"call_{uuid4().hex[:12]}"
        if pc.name:  # require a name to be usable
            pending.append(pc)

    return text_buf, pending, (prompt_tokens, completion_tokens)


# ------------------------------------------------------------------
#  Tool execution
# ------------------------------------------------------------------


async def _run_tool_calls(
    pending: list[_PendingToolCall],
    *,
    sandbox_id: str,
    session_id,
    run_id,
    working_messages: list,
    manifests: list,
    mcp_tool_models: dict[str, str] | None = None,
    billing_enabled: bool = False,
    operation_scope: str = "0:0",
) -> _ExecutionProof:
    """Invoke each tool via MCP Hub, emit events, append tool messages."""
    mcp = get_client()
    manifest_by_name = {m.name: m for m in manifests}
    proof = _ExecutionProof()
    for call_index, pc in enumerate(pending):
        args = _parse_args(pc.args_buf)
        if pc.name == ASK_USER_TOOL_NAME:
            questions = _parse_ask_user_questions(args)
            result = ToolResult(
                ok=True,
                preview="已向用户弹出确认卡片，等待选择后继续。",
                output={"waiting_user": True, "questions": questions},
            )
            await emit(
                ToolCallEvent(
                    session_id=session_id,
                    run_id=run_id,
                    id=pc.id,
                    name=pc.name,
                    args=args,
                )
            )
            await emit(
                ToolResultEvent(
                    session_id=session_id,
                    run_id=run_id,
                    id=pc.id,
                    ok=True,
                    preview=result.preview,
                    duration_ms=0,
                )
            )
            working_messages.append(
                ToolMessage(
                    content=_render_tool_content(result),
                    tool_call_id=pc.id,
                    name=pc.name,
                )
            )
            proof = _merge_proof(proof, _ExecutionProof(user_questions=questions))
            continue
        result, delta = await _invoke_tool_with_events(
            mcp=mcp,
            tool_name=pc.name,
            args=args,
            event_id=pc.id,
            sandbox_id=sandbox_id,
            session_id=session_id,
            run_id=run_id,
            working_messages=working_messages,
            manifest=manifest_by_name.get(pc.name),
            mcp_tool_models=mcp_tool_models,
            billing_enabled=billing_enabled,
            operation_index=f"{operation_scope}:{call_index}",
        )
        proof = _merge_proof(proof, delta)
        _result, extra = await _retry_failed_tool_if_needed(
            mcp=mcp,
            tool_name=pc.name,
            args=args,
            result=result,
            event_id=pc.id,
            sandbox_id=sandbox_id,
            session_id=session_id,
            run_id=run_id,
            working_messages=working_messages,
            manifest_by_name=manifest_by_name,
            mcp_tool_models=mcp_tool_models,
            billing_enabled=billing_enabled,
            operation_prefix=f"{operation_scope}:{call_index}",
        )
        proof = _merge_proof(proof, extra)
    return proof


def _render_tool_content(result) -> str:
    """Compact text payload the LLM sees as the tool return value."""
    body: dict[str, Any] = {
        "ok": result.ok,
        "preview": result.preview[:4000] if result.preview else "",
    }
    if result.error:
        body["error"] = result.error
    if result.duration_ms is not None:
        body["duration_ms"] = result.duration_ms
    # Keep a trimmed output so the model has structured data when helpful.
    if result.output is not None:
        try:
            raw = json.dumps(result.output, ensure_ascii=False)
        except (TypeError, ValueError):
            raw = str(result.output)
        body["output"] = raw[:4000]
    return json.dumps(body, ensure_ascii=False)


def _is_publishable_artifact_relpath(path: str) -> bool:
    """Skip junk paths recovered from shell/logs（如代码片段误当作文件名）。"""
    s = (path or "").strip().replace("\\", "/")
    if not s or len(s) > 512:
        return False
    if ".." in PurePosixPath(s).parts:
        return False
    if any(c in s for c in "{}"):
        return False
    if "[:" in s:
        return False
    base = Path(s).name
    if not base or len(base) > 240:
        return False
    return True


def _artifact_url(session_id, path: str) -> str:
    """Browser-facing URL. When NEXT_PUBLIC_API_BASE is unset, use Next.js /api proxy."""
    base = (get_settings().public_api_base or "").strip().rstrip("/")
    q = quote(path, safe="")
    tail = f"/v1/sessions/{session_id}/artifacts/content?path={q}"
    if not base:
        return f"/api{tail}"
    return f"{base}{tail}"


async def _emit_artifact_events(
    *,
    session_id,
    run_id: str | None,
    proof: _ExecutionProof,
) -> None:
    seen: set[str] = set()
    for path in sorted(proof.written_paths):
        if path in seen:
            continue
        seen.add(path)
        if not _is_publishable_artifact_relpath(path):
            continue
        suffix = Path(path).suffix.lower()
        if not suffix:
            continue
        mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
        url = _artifact_url(session_id, path)
        await emit(
            ArtifactEvent(
                session_id=session_id,
                run_id=run_id,
                name=Path(path).name,
                mime=mime,
                url=url,
                description=path,
            )
        )
        if mime.startswith("image/"):
            await emit(
                ScreenshotEvent(
                    session_id=session_id,
                    run_id=run_id,
                    url=url,
                    source="custom",
                )
            )


async def _invoke_tool_with_events(
    *,
    mcp,
    tool_name: str,
    args: dict[str, Any],
    event_id: str,
    sandbox_id: str,
    session_id,
    run_id,
    working_messages: list,
    manifest,
    mcp_tool_models: dict[str, str] | None = None,
    billing_enabled: bool = False,
    operation_index: str | None = None,
) -> tuple[Any, _ExecutionProof]:
    eff_args = args
    if (
        tool_name in _MCP_TOOLS_OPTIONAL_MODEL
        and mcp_tool_models
        and not str(args.get("model") or "").strip()
    ):
        dm = (mcp_tool_models.get(tool_name) or "").strip()
        if dm:
            eff_args = {**args, "model": dm}
    operation_key = _tool_operation_key(
        run_id=run_id,
        operation_index=operation_index or event_id,
        tool_name=tool_name,
        args=eff_args,
    )
    if billing_enabled and run_id:
        allowed, required, current = await reserve_tool_points(
            run_id=run_id,
            idempotency_key=f"{operation_key}:reserve",
            tool_name=tool_name,
            args=eff_args,
        )
        if not allowed:
            if required <= 0:
                error_message = "当前任务计费状态已关闭，已阻止重复工具调用"
            else:
                error_message = (
                    f"积分不足，调用 {tool_name} 还需冻结 {required} 积分，当前可用 {current}"
                )
            result = ToolResult(
                ok=False,
                error=error_message,
            )
            await emit(
                ToolCallEvent(
                    session_id=session_id,
                    run_id=run_id,
                    id=event_id,
                    name=tool_name,
                    args=eff_args,
                )
            )
            await emit(
                ToolResultEvent(
                    session_id=session_id,
                    run_id=run_id,
                    id=event_id,
                    ok=False,
                    preview=result.error or "积分不足",
                    duration_ms=0,
                )
            )
            working_messages.append(
                ToolMessage(content=_render_tool_content(result), tool_call_id=event_id, name=tool_name)
            )
            return result, _ExecutionProof()
    await emit(
        ToolCallEvent(
            session_id=session_id,
            run_id=run_id,
            id=event_id,
            name=tool_name,
            args=eff_args,
        )
    )
    started = time.perf_counter()
    result = await mcp.invoke(
        tool_name,
        sandbox_id=sandbox_id,
        args=eff_args,
        session_id=str(session_id),
        run_id=run_id,
        idempotency_key=operation_key,
    )
    duration_ms = result.duration_ms
    if duration_ms is None:
        duration_ms = int((time.perf_counter() - started) * 1000)

    preview = result.preview or (result.error or "")
    await emit(
        ToolResultEvent(
            session_id=session_id,
            run_id=run_id,
            id=event_id,
            ok=result.ok,
            preview=preview[:2000],
            duration_ms=duration_ms,
        )
    )
    if billing_enabled:
        await record_tool_usage(
            run_id=run_id,
            idempotency_key=operation_key,
            tool_name=tool_name,
            args=eff_args,
            result=result,
        )

    working_messages.append(
        ToolMessage(content=_render_tool_content(result), tool_call_id=event_id, name=tool_name)
    )
    proof = _proof_from_tool_result(
        tool_name=tool_name,
        args=eff_args,
        result=result,
        manifest=manifest,
    )
    if tool_name == "shell" and result.ok:
        extra_paths = await _confirm_shell_artifact_paths_via_stat(
            mcp=mcp,
            sandbox_id=sandbox_id,
            shell_args=args,
            shell_result=result,
        )
        proof.written_paths.update(extra_paths)

    await _emit_artifact_events(
        session_id=session_id,
        run_id=run_id,
        proof=proof,
    )
    return result, proof


def _tool_operation_key(
    *,
    run_id: str | None,
    operation_index: str,
    tool_name: str,
    args: dict[str, Any],
) -> str:
    """Build a retry-stable key from the logical position and effective arguments."""
    canonical = json.dumps(args, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    args_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:20]
    return f"{run_id or 'no-run'}:tool:{operation_index}:{tool_name}:{args_hash}"


def _detect_shell_recovery(*, args: dict[str, Any], result) -> _ShellRecoveryPlan | None:
    if result.ok:
        return None

    output = result.output if isinstance(result.output, dict) else {}
    cmd = str(output.get("cmd") or args.get("cmd") or "")
    stdout = str(output.get("stdout") or "")
    text = "\n".join(x for x in [result.error or "", result.preview or "", stdout] if x)
    lowered = text.lower()
    cmd_lower = cmd.lower()

    if any(token in cmd_lower for token in ("pip install", "uv pip install", "npm install", "pnpm install", "yarn add")):
        return None

    py_match = _PYTHON_MODULE_RE.search(text)
    if py_match:
        module = py_match.group(1).split(".")[0]
        package = _PYTHON_PACKAGE_MAP.get(module, module)
        return _ShellRecoveryPlan(
            reason=f"python module missing: {module}",
            install_cmd=f"python -m pip install {shlex.quote(package)}",
        )

    node_match = _NODE_MODULE_RE.search(text)
    if node_match:
        package = _normalize_node_package_name(node_match.group(1))
        return _ShellRecoveryPlan(
            reason=f"node module missing: {package}",
            install_cmd=f"npm install {shlex.quote(package)}",
        )

    bin_match = _MISSING_BIN_RE.search(text)
    if bin_match:
        missing_bin = bin_match.group(1)
        if missing_bin in {"next", "vite", "webpack", "ts-node", "react-scripts"}:
            return _ShellRecoveryPlan(
                reason=f"node binary missing from local deps: {missing_bin}",
                install_cmd="if [ -f package.json ]; then npm install; else exit 1; fi",
            )

    if "no such file or directory, open 'package.json'" in lowered:
        return None

    return None


async def _retry_failed_tool_if_needed(
    *,
    mcp,
    tool_name: str,
    args: dict[str, Any],
    result,
    event_id: str,
    sandbox_id: str,
    session_id,
    run_id,
    working_messages: list,
    manifest_by_name: dict[str, Any],
    mcp_tool_models: dict[str, str] | None,
    billing_enabled: bool,
    operation_prefix: str,
) -> tuple[Any, _ExecutionProof]:
    """Count a failed call; pip-recover missing deps or once-retry timeout/nonzero shell."""
    extra = _ExecutionProof()
    if result.ok:
        return result, extra

    extra.failed_tool_calls = 1
    extra.failure_notes = [_failure_note(result)]

    recovery = _detect_shell_recovery(args=args, result=result)
    if recovery is not None:
        log.info(
            "graph.execute.shell_auto_recover",
            session_id=str(session_id),
            run_id=run_id,
            reason=recovery.reason,
            install_cmd=recovery.install_cmd,
        )
        install_result, install_proof = await _invoke_tool_with_events(
            mcp=mcp,
            tool_name="shell",
            args={"cmd": recovery.install_cmd, "cwd": args.get("cwd")},
            event_id=f"{event_id}_recover_install",
            sandbox_id=sandbox_id,
            session_id=session_id,
            run_id=run_id,
            working_messages=working_messages,
            manifest=manifest_by_name.get("shell"),
            mcp_tool_models=mcp_tool_models,
            billing_enabled=billing_enabled,
            operation_index=f"{operation_prefix}:recover_install",
        )
        extra = _merge_proof(extra, install_proof)
        if install_result.ok:
            retry_result, retry_proof = await _invoke_tool_with_events(
                mcp=mcp,
                tool_name=tool_name,
                args=args,
                event_id=f"{event_id}_recover_retry",
                sandbox_id=sandbox_id,
                session_id=session_id,
                run_id=run_id,
                working_messages=working_messages,
                manifest=manifest_by_name.get(tool_name),
                mcp_tool_models=mcp_tool_models,
                billing_enabled=billing_enabled,
                operation_index=f"{operation_prefix}:recover_retry",
            )
            extra = _merge_proof(extra, retry_proof)
            if retry_result.ok:
                extra.recovered_failures = 1
            return retry_result, extra
        return result, extra

    if _is_retryable_shell_failure(tool_name=tool_name, result=result):
        log.info(
            "graph.execute.shell_fail_retry",
            session_id=str(session_id),
            run_id=run_id,
            note=extra.failure_notes[-1] if extra.failure_notes else "tool failed",
        )
        retry_result, retry_proof = await _invoke_tool_with_events(
            mcp=mcp,
            tool_name=tool_name,
            args=args,
            event_id=f"{event_id}_fail_retry",
            sandbox_id=sandbox_id,
            session_id=session_id,
            run_id=run_id,
            working_messages=working_messages,
            manifest=manifest_by_name.get(tool_name),
            mcp_tool_models=mcp_tool_models,
            billing_enabled=billing_enabled,
            operation_index=f"{operation_prefix}:fail_retry",
        )
        extra = _merge_proof(extra, retry_proof)
        if retry_result.ok:
            extra.recovered_failures = 1
        return retry_result, extra

    return result, extra


def _normalize_evidence_path(raw: Any, *, cwd: str | None = None) -> str | None:
    if not isinstance(raw, str):
        return None
    path = raw.strip().strip("\"'")
    if not path or path in {".", ".."} or path.startswith("-") or path.startswith("$"):
        return None
    p = Path(path)
    if cwd and not p.is_absolute():
        p = Path(cwd) / p
    return str(p)


def _shell_command_paths(cmd: str, *, cwd: str | None, mode: str) -> set[str]:
    if not cmd:
        return set()

    paths: set[str] = set()

    def _add(raw: Any) -> None:
        normalized = _normalize_evidence_path(raw, cwd=cwd)
        if normalized:
            paths.add(normalized)

    for part in re.split(r"\s*(?:&&|\|\||;|\n)\s*", cmd):
        subcmd = part.strip()
        if not subcmd:
            continue
        if mode == "write":
            for match in re.findall(r"(?:>|>>)\s*([^\s;&|]+)", subcmd):
                _add(match)
            for match in re.findall(r"\btee\s+([^\s;&|]+)", subcmd):
                _add(match)
        try:
            tokens = shlex.split(subcmd)
        except ValueError:
            tokens = []
        if not tokens:
            continue

        binary = tokens[0]
        non_flag_args = [tok for tok in tokens[1:] if not tok.startswith("-")]
        if mode == "write":
            if binary in {"touch", "mkdir"}:
                for arg in non_flag_args:
                    _add(arg)
            elif binary in {"cp", "mv"} and len(non_flag_args) >= 2:
                _add(non_flag_args[-1])
            elif binary == "npm" and len(tokens) >= 2 and tokens[1] == "init":
                _add("package.json")
        else:
            if binary in {"cat", "head", "tail", "sed", "stat"} and non_flag_args:
                _add(non_flag_args[-1])
            elif binary == "ls":
                if non_flag_args:
                    for arg in non_flag_args:
                        _add(arg)
                elif cwd:
                    _add(cwd)
            elif binary == "test" and len(tokens) >= 3 and tokens[1] in {"-f", "-d", "-e"}:
                _add(tokens[2])
    return paths


_HOST_SANDBOX = re.compile(r"/sandboxes/[0-9a-fA-F\-]{8,}/")


def _workspace_relative_from_guess(path: str) -> str:
    """Strip /var/.../sandboxes/<id>/ so open(relpath) works in the sandbox cwd."""
    path = path.strip().replace("\\", "/")
    if not path or "://" in path:
        return ""
    m = _HOST_SANDBOX.search(path)
    if m:
        return path[m.end() :].lstrip("./")
    return path.lstrip("./")


def _guess_shell_artifact_paths(*, cmd: str, stdout: str, preview: str) -> set[str]:
    """Paths written by Python libs (savefig, PIL) never hit shell `>` redirects — recover from cmd/log."""
    blob = "\n".join([cmd[:400_000], stdout[:64_000], preview[:16_000]])
    found: set[str] = set()

    for pat in (
        r"(?i)\bwrote\s+(?:\d+\s+)?bytes\s*->\s*(\S+)",
        r"(?is)Image\.save\(\s*['\"]([^'\"]+\.(?:png|jpe?g|gif|webp))['\"]",
        r"(?is)savefig\(\s*['\"]([^'\"]+\.(?:png|jpe?g|pdf|svg))['\"]",
        r"(?is)\.save\(\s*['\"]([^'\"]+\.(?:png|jpe?g|gif|webp|pdf))['\"]",
        r"(?is)to_file\(\s*['\"]([^'\"]+\.(?:png|html))['\"]",
        # 中文日志常见「已生成 / 成功：xxx.png」（无引号），模式二里 matplotlib 打印易落入此类
        r"(?m)(?:已成功生成|成功生成|已生成|生成成功|保存为|输出到|词云图)[^:：\n]{0,40}?[：:]\s*([a-zA-Z0-9_.\-/]{1,220}\.(?:png|jpe?g|gif|webp|svg|pdf))",
        r"(?m)✅\s*[^\n]*?[：:]\s*([a-zA-Z0-9_.\-/]{1,220}\.(?:png|jpe?g|gif|webp|svg|pdf))",
    ):
        for m in re.finditer(pat, blob):
            p = _workspace_relative_from_guess(m.group(1))
            if p and len(p) < 500:
                found.add(p)

    for m in re.finditer(
        r'''["']([a-zA-Z0-9_./\-]{1,200}\.(?:png|jpe?g|gif|webp|svg|pdf|markdown|md|html|csv|json))["']''',
        blob,
        re.IGNORECASE,
    ):
        raw = m.group(1)
        if any(x in raw for x in ("node_modules", "site-packages", "http://", "https://")):
            continue
        p = _workspace_relative_from_guess(raw)
        if p and len(p) < 500:
            found.add(p)

    return found


_BARE_DELIVERABLE_NAME = re.compile(
    r"(?<![A-Za-z0-9_./-])([a-zA-Z0-9_./-]{1,220}"
    r"\.(?:png|jpe?g|gif|webp|svg|pdf|markdown|md|html?|csv|json|txt|pptx?))\b",
    re.IGNORECASE,
)


def _bare_deliverable_filenames_in_text(text: str) -> set[str]:
    """从 stdout / preview 里抽无引号的交付物文件名，供 stat 探针使用。"""
    found: set[str] = set()
    for m in _BARE_DELIVERABLE_NAME.finditer(text or ""):
        raw = m.group(1)
        if any(x in raw for x in ("node_modules", "site-packages", "http://", "https://")):
            continue
        p = _workspace_relative_from_guess(raw)
        if p and len(p) < 500 and ".." not in Path(p).parts:
            found.add(p)
    return found


async def _confirm_shell_artifact_paths_via_stat(
    *,
    mcp,
    sandbox_id: str,
    shell_args: dict[str, Any],
    shell_result,
    max_stats: int = 24,
) -> set[str]:
    """shell 成功后对候选路径做 filesystem stat，不依赖 stdout 固定文案，用于稳定触发 Artifact。"""
    output = shell_result.output if isinstance(shell_result.output, dict) else {}
    cmd = str(output.get("cmd") or shell_args.get("cmd") or "")
    stdout = str(output.get("stdout") or "")
    preview = str(shell_result.preview or "")
    cwd = output.get("cwd") if isinstance(output, dict) else None

    candidates: set[str] = set()
    candidates |= _guess_shell_artifact_paths(cmd=cmd, stdout=stdout, preview=preview)
    candidates |= _shell_command_paths(cmd, cwd=cwd, mode="write")
    candidates |= _bare_deliverable_filenames_in_text(stdout)
    candidates |= _bare_deliverable_filenames_in_text(preview)

    confirmed: set[str] = set()
    tried = 0
    for raw in sorted(candidates):
        if tried >= max_stats:
            break
        rel = raw.strip().lstrip("./")
        if not rel or ".." in Path(rel).parts:
            continue
        if not _is_deliverable_path_candidate(rel):
            continue
        tried += 1
        st = await mcp.invoke(
            "filesystem",
            sandbox_id=sandbox_id,
            args={"action": "stat", "path": rel},
        )
        if not st.ok:
            continue
        body = st.output if isinstance(st.output, dict) else {}
        if not body.get("is_file"):
            continue
        use_rel = rel.strip().lstrip("./")
        if not use_rel or not _is_deliverable_path_candidate(use_rel):
            continue
        if _thin_written_file_reason(
            path=use_rel,
            exists=True,
            is_file=True,
            size=body.get("size"),
        ):
            continue
        confirmed.add(use_rel)
    return confirmed


# 经 stat 确认的交付物（脚本写出的 PNG 等若未进入 shell 的 written_paths，靠 stat 补 artifact）
_DELIVERABLE_EXTS_FOR_STAT = frozenset({
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".svg",
    ".pdf",
    ".md",
    ".markdown",
    ".html",
    ".htm",
    ".csv",
    ".json",
    ".txt",
    ".pptx",
    ".ppt",
    ".mp4",
    ".mp3",
    ".webm",
    ".wav",
})


def _is_deliverable_path_candidate(rel: str) -> bool:
    s = Path(rel.strip()).suffix.lower()
    return bool(s) and s in _DELIVERABLE_EXTS_FOR_STAT


def _stat_rel_path(raw: str) -> str | None:
    rel = _workspace_relative_from_guess(raw) or str(raw).strip().lstrip("./")
    if not rel or ".." in Path(rel).parts:
        return None
    return rel


async def _empty_written_delivery_reason(
    *,
    mcp,
    sandbox_id: str,
    proof: _ExecutionProof,
    requires_artifact: bool,
    max_stats: int = 12,
) -> str | None:
    if not requires_artifact:
        return None
    candidates = [
        p
        for p in sorted(proof.written_paths)
        if isinstance(p, str) and _is_deliverable_path_candidate(_stat_rel_path(p) or p)
    ]
    if not candidates:
        return None
    first_bad: str | None = None
    ok_files = 0
    tried = 0
    for raw in candidates:
        if tried >= max_stats:
            break
        rel = _stat_rel_path(raw)
        if not rel:
            continue
        tried += 1
        st = await mcp.invoke(
            "filesystem",
            sandbox_id=sandbox_id,
            args={"action": "stat", "path": rel},
        )
        if not st.ok:
            if first_bad is None:
                first_bad = f"声称已写入 {rel}，但文件不存在或不可读"
            continue
        body = st.output if isinstance(st.output, dict) else {}
        if body.get("is_dir") and not body.get("is_file"):
            continue
        reason = _thin_written_file_reason(
            path=rel,
            exists=True,
            is_file=bool(body.get("is_file")),
            size=body.get("size"),
        )
        if reason:
            if first_bad is None:
                first_bad = reason
            continue
        if body.get("is_file"):
            ok_files += 1
    if ok_files > 0:
        return None
    return first_bad


async def _stop_blocked_reason(
    *,
    user_message: str,
    plan: dict[str, Any] | None,
    proof: _ExecutionProof,
    task_frame: dict[str, Any] | None,
    sandbox_id: str,
    mcp=None,
) -> str | None:
    reason = _missing_delivery_reason(
        user_message=user_message,
        plan=plan,
        proof=proof,
        task_frame=task_frame,
    )
    if reason:
        return reason
    reason = _unrecovered_failure_reason(proof)
    if reason:
        return reason
    requires_artifact = _goal_requires_real_artifact(user_message, plan, task_frame)
    return await _empty_written_delivery_reason(
        mcp=mcp or get_client(),
        sandbox_id=sandbox_id,
        proof=proof,
        requires_artifact=requires_artifact,
    )


def _proof_from_tool_result(
    *,
    tool_name: str,
    args: dict[str, Any],
    result,
    manifest,
) -> _ExecutionProof:
    proof = _ExecutionProof()
    if not result.ok:
        return proof

    proof.successful_tool_calls = 1
    proof.tool_names.append(tool_name)
    if tool_name != "search":
        proof.non_search_tool_calls = 1
    if getattr(manifest, "mutates", False):
        proof.mutating_tool_calls = 1

    output = result.output if isinstance(result.output, dict) else {}
    if tool_name == "search" and output.get("provider") == "mock":
        proof.mock_search_calls = 1
    if tool_name == "filesystem":
        action = args.get("action")
        if action in {"write", "append"}:
            path = output.get("path")
            if isinstance(path, str) and path:
                proof.written_paths.add(path)
        elif action == "stat":
            path_arg = args.get("path")
            path_out = output.get("path") if isinstance(output, dict) else None
            for raw in (path_arg, path_out):
                normalized = _normalize_evidence_path(raw)
                if normalized:
                    proof.verified_paths.add(normalized)
        elif action == "read":
            path = output.get("path") or args.get("path")
            normalized = _normalize_evidence_path(path)
            if normalized:
                proof.verified_paths.add(normalized)
        elif action == "list":
            for entry in (output.get("entries") or [])[:50]:
                normalized = _normalize_evidence_path(entry.get("path") if isinstance(entry, dict) else None)
                if normalized:
                    proof.verified_paths.add(normalized)
    elif tool_name == "shell":
        cwd = output.get("cwd") if isinstance(output, dict) else None
        cmd = str(output.get("cmd") or args.get("cmd") or "")
        proof.written_paths.update(_shell_command_paths(cmd, cwd=cwd, mode="write"))
        proof.verified_paths.update(_shell_command_paths(cmd, cwd=cwd, mode="verify"))
        stdout = str(output.get("stdout") or "")
        preview_txt = str(result.preview or "")
        proof.written_paths.update(
            _guess_shell_artifact_paths(cmd=cmd, stdout=stdout, preview=preview_txt)
        )
    elif tool_name in {
        "wan_text2image",
        "wan_t2v",
        "wan_i2v",
        "wan_r2v",
        "wan_video_edit",
        "minimax_tts",
    } and isinstance(output, dict):
        raw_paths: list[str] = []
        paths_val = output.get("paths")
        if isinstance(paths_val, list):
            for item in paths_val:
                if isinstance(item, str) and item.strip():
                    raw_paths.append(item)
        p1 = output.get("path")
        if isinstance(p1, str) and p1.strip():
            raw_paths.append(p1)
        for raw in raw_paths:
            rel = _workspace_relative_from_guess(raw)
            if not rel:
                rel = raw.strip().lstrip("./")
            if rel and ".." not in Path(rel).parts:
                proof.written_paths.add(rel)
    return proof
