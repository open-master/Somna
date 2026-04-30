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

from dataclasses import dataclass, field
import json
import mimetypes
from pathlib import Path
import re
import shlex
import time
from typing import Any
from urllib.parse import quote
from uuid import uuid4

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from somna_events import (
    ArtifactEvent,
    MessageDeltaEvent,
    ScreenshotEvent,
    ToolCallEvent,
    ToolResultEvent,
    TokenUsageEvent,
)

from app.config import get_settings
from app.events.emitter import emit
from app.graph.compact import maybe_compact
from app.graph.model_policy import pick_executor_turn_model
from app.graph.nodes.plan import advance_with_proof, mark_progress
from app.graph.state import SessionState
from app.llm.client import get_async_openai
from app.logging_setup import get_logger
from app.memory import format_memories, search_memories
from app.prompts.loader import build_system_prompt
from app.tools.client import get_client
from app.tools.schema import manifests_to_openai_tools, openai_tool_choice, tool_manifest_cache

log = get_logger(__name__)


@dataclass
class _ExecutionProof:
    successful_tool_calls: int = 0
    mutating_tool_calls: int = 0
    non_search_tool_calls: int = 0
    mock_search_calls: int = 0
    written_paths: set[str] = field(default_factory=set)
    verified_paths: set[str] = field(default_factory=set)


@dataclass
class _ShellRecoveryPlan:
    reason: str
    install_cmd: str


def _merge_proof(base: _ExecutionProof, delta: _ExecutionProof) -> _ExecutionProof:
    base.successful_tool_calls += delta.successful_tool_calls
    base.mutating_tool_calls += delta.mutating_tool_calls
    base.non_search_tool_calls += delta.non_search_tool_calls
    base.mock_search_calls += delta.mock_search_calls
    base.written_paths.update(delta.written_paths)
    base.verified_paths.update(delta.verified_paths)
    return base


def _goal_requires_real_artifact(user_message: str, plan: dict[str, Any] | None) -> bool:
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
) -> str | None:
    plan_todos = (plan or {}).get("todos", []) if isinstance(plan, dict) else []
    requires_artifact = _goal_requires_real_artifact(user_message, plan)

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


def _artifact_paths(proof: _ExecutionProof) -> set[str]:
    return set(proof.written_paths) | set(proof.verified_paths)


def _summarize_execution(proof: _ExecutionProof, *, delivery_missing_reason: str | None = None) -> dict[str, Any]:
    return {
        "successful_tool_calls": proof.successful_tool_calls,
        "mutating_tool_calls": proof.mutating_tool_calls,
        "non_search_tool_calls": proof.non_search_tool_calls,
        "mock_search_calls": proof.mock_search_calls,
        "written_paths": sorted(proof.written_paths),
        "verified_paths": sorted(proof.verified_paths),
        "delivery_missing_reason": delivery_missing_reason,
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
    if engine in ("anthropic", "anthropic_compat", "mode2"):
        from app.graph.nodes.execute_agent_sdk import execute_agent_sdk_node

        return await execute_agent_sdk_node(state)

    settings = get_settings()
    session_id = state["session_id"]
    run_id = state.get("run_id")
    exec_alias = (state.get("executor_model") or settings.agent_default_executor).strip()
    coder_alias = (state.get("coder_model") or settings.agent_default_coder).strip()
    sandbox_id = state.get("sandbox_id") or str(session_id)

    client = get_async_openai()
    manifests = list(tool_manifest_cache().values())
    manifest_by_name = {m.name: m for m in manifests}
    tools_schema = manifests_to_openai_tools(manifests) if manifests else None

    # Pull relevant long-term memories so the executor prompt starts with
    # whatever we already know about this user. No-op when memory is off.
    user_message = state.get("user_message") or ""
    memories = await search_memories(
        user_message,
        session_id=str(session_id),
        user_id=state.get("user_id"),
    )
    memory_block = format_memories(memories)
    extra_context = (
        f"### 用户长期记忆（来自 mem0）\n{memory_block}" if memory_block else None
    )

    system_prompt = build_system_prompt(
        session_id=str(session_id),
        user_id=state.get("user_id"),
        manifests=manifests,
        extra_context=extra_context,
    )

    # Compose initial messages: system + history.
    working_messages: list = list(state.get("messages") or [])
    if not any(isinstance(m, SystemMessage) for m in working_messages):
        working_messages = [SystemMessage(content=system_prompt)] + working_messages

    log.info(
        "graph.execute.start",
        session_id=str(session_id),
        executor_model=exec_alias,
        coder_model=coder_alias,
        n_msgs=len(working_messages),
        tools=len(tools_schema or []),
    )

    prompt_tokens_total = completion_tokens_total = 0
    tool_turns = int(state.get("tool_turns") or 0)
    max_turns = settings.agent_max_turns
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

    try:
        while tool_turns < max_turns:
            # Compact older history if the context is getting heavy.
            working_messages, did_compact, summary = await maybe_compact(
                working_messages,
                session_id=session_id,
                run_id=run_id,
                compact_model=state.get("compact_model"),
                longctx_model=state.get("longctx_model"),
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
            prompt_tokens_total += usage[0]
            completion_tokens_total += usage[1]
            if usage[0] or usage[1]:
                await emit(
                    TokenUsageEvent(
                        session_id=session_id,
                        run_id=run_id,
                        model=turn_model,
                        input=usage[0],
                        output=usage[1],
                        cost_usd=0.0,
                    )
                )

            # Append assistant turn to conversation.
            assistant_kwargs = {"content": turn_text}
            if pending_calls:
                assistant_kwargs["additional_kwargs"] = {
                    "tool_calls": [pc.to_dict() for pc in pending_calls]
                }
            working_messages.append(AIMessage(**assistant_kwargs))

            if not pending_calls:
                reason = _missing_delivery_reason(
                    user_message=user_message,
                    plan=plan,
                    proof=proof,
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
                    if finish_validation_failures >= 2:
                        plan = await mark_progress(
                            plan, session_id=session_id, run_id=run_id, fail_current=True
                        )
                        return {
                            "assistant_text": turn_text,
                            "messages": [m for m in working_messages if not isinstance(m, SystemMessage)],
                            "compact_memory": compact_memory,
                            "tool_turns": tool_turns,
                            "plan": plan,
                            "execution_summary": _summarize_execution(
                                proof, delivery_missing_reason=reason
                            ),
                            "error": f"模型试图结束运行，但未检测到真实交付证据：{reason}",
                            "finished": True,
                        }
                    forced_tool_name = _delivery_recovery_tool_name(manifests)
                    working_messages.append(SystemMessage(content=_completion_retry_message(reason)))
                    continue
                forced_tool_name = None
                final_text = turn_text
                break

            tool_turns += 1
            turn_proof = await _run_tool_calls(
                pending_calls,
                sandbox_id=sandbox_id,
                session_id=session_id,
                run_id=run_id,
                working_messages=working_messages,
                manifests=manifests,
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
        else:
            log.warning("graph.execute.max_turns", session_id=str(session_id), turns=tool_turns)
            final_text = (
                "（已达到最大工具调用轮数上限，未能完成任务。请尝试拆小或直接提问。）"
            )
            plan = await mark_progress(
                plan, session_id=session_id, run_id=run_id, fail_current=True
            )
    except Exception as exc:  # noqa: BLE001
        log.exception("graph.execute.failed", error=str(exc))
        plan = await mark_progress(
            plan, session_id=session_id, run_id=run_id, fail_current=True
        )
        return {
            "error": str(exc),
            "finished": True,
            "plan": plan,
            "execution_summary": _summarize_execution(proof),
        }

    log.info(
        "graph.execute.done",
        session_id=str(session_id),
        turns=tool_turns,
        chars=len(final_text),
        in_tokens=prompt_tokens_total,
        out_tokens=completion_tokens_total,
    )

    # Drop the injected SystemMessage if we added one (it's rebuilt each run).
    new_messages = [m for m in working_messages if not isinstance(m, SystemMessage)]

    return {
        "assistant_text": final_text,
        "messages": new_messages,
        "compact_memory": compact_memory,
        "tool_turns": tool_turns,
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
) -> _ExecutionProof:
    """Invoke each tool via MCP Hub, emit events, append tool messages."""
    mcp = get_client()
    manifest_by_name = {m.name: m for m in manifests}
    proof = _ExecutionProof()
    for pc in pending:
        args = _parse_args(pc.args_buf)
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
        )
        proof = _merge_proof(proof, delta)

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
                event_id=f"{pc.id}_recover_install",
                sandbox_id=sandbox_id,
                session_id=session_id,
                run_id=run_id,
                working_messages=working_messages,
                manifest=manifest_by_name.get("shell"),
            )
            proof = _merge_proof(proof, install_proof)
            if install_result.ok:
                retry_result, retry_proof = await _invoke_tool_with_events(
                    mcp=mcp,
                    tool_name=pc.name,
                    args=args,
                    event_id=f"{pc.id}_recover_retry",
                    sandbox_id=sandbox_id,
                    session_id=session_id,
                    run_id=run_id,
                    working_messages=working_messages,
                    manifest=manifest_by_name.get(pc.name),
                )
                proof = _merge_proof(proof, retry_proof)
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
) -> tuple[Any, _ExecutionProof]:
    await emit(
        ToolCallEvent(
            session_id=session_id,
            run_id=run_id,
            id=event_id,
            name=tool_name,
            args=args,
        )
    )
    started = time.perf_counter()
    result = await mcp.invoke(
        tool_name,
        sandbox_id=sandbox_id,
        args=args,
        session_id=str(session_id),
        run_id=run_id,
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

    working_messages.append(
        ToolMessage(content=_render_tool_content(result), tool_call_id=event_id, name=tool_name)
    )
    proof = _proof_from_tool_result(
        tool_name=tool_name,
        args=args,
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
    r"\.(?:png|jpe?g|gif|webp|svg|pdf|markdown|md|html|csv|json|txt))\b",
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
        if use_rel and _is_deliverable_path_candidate(use_rel):
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
    ".csv",
    ".json",
    ".txt",
})


def _is_deliverable_path_candidate(rel: str) -> bool:
    s = Path(rel.strip()).suffix.lower()
    return bool(s) and s in _DELIVERABLE_EXTS_FOR_STAT


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
            if isinstance(output, dict) and output.get("is_file") is True:
                rel: str | None = None
                if isinstance(path_arg, str) and path_arg.strip() not in {".", "..", ""}:
                    rel = path_arg.strip().lstrip("./")
                if not rel and isinstance(path_out, str):
                    rel = Path(path_out).name
                if rel and _is_deliverable_path_candidate(rel):
                    proof.written_paths.add(rel)
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
    return proof
