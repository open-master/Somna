"""execute node — Anthropic Messages protocol via LiteLLM (same MCP tools, same aliases).

模式二：走 LiteLLM 统一 `POST {LITELLM}/v1/messages`（Anthropic SDK + 根 base_url），
模型名仍为 `agent-executor` 等，与模式一相同别名路由，仅协议形状不同。
勿用 `/anthropic/v1/messages`：该路径为官方 Anthropic 直通，不解析 agent-* 别名。
"""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from somna_events import MessageDeltaEvent, TokenUsageEvent

from app.config import get_settings
from app.events.emitter import emit
from app.graph.compact import maybe_compact
from app.graph.model_policy import pick_executor_turn_model
from app.graph.nodes.plan import advance_with_proof, mark_progress
from app.graph.state import SessionState
from app.llm.client import get_async_anthropic
from app.logging_setup import get_logger
from app.memory import format_memories, search_memories
from app.prompts.loader import build_system_prompt
from app.tools.schema import (
    anthropic_tool_choice,
    manifests_to_anthropic_tools,
    tool_manifest_cache,
)

from app.graph.nodes.execute import (
    _ExecutionProof,
    _PendingToolCall,
    _completion_retry_message,
    _content_str,
    _delivery_recovery_tool_name,
    _merge_proof,
    _missing_delivery_reason,
    _proof_from_execution_summary,
    _run_tool_calls,
    _summarize_execution,
)

log = get_logger(__name__)

_ANTHROPIC_MAX_TOKENS = 16_384


def _langchain_body_to_anthropic_messages(msgs: list) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    pending_tool_results: list[dict[str, Any]] = []

    def flush_tools() -> None:
        nonlocal pending_tool_results
        if pending_tool_results:
            out.append({"role": "user", "content": pending_tool_results[:]})
            pending_tool_results = []

    for m in msgs:
        if isinstance(m, SystemMessage):
            continue
        if isinstance(m, HumanMessage):
            flush_tools()
            out.append({"role": "user", "content": _content_str(m.content)})
        elif isinstance(m, AIMessage):
            flush_tools()
            blocks: list[dict[str, Any]] = []
            text = _content_str(m.content)
            if text:
                blocks.append({"type": "text", "text": text})
            tc_raw = getattr(m, "tool_calls", None) or m.additional_kwargs.get("tool_calls") or []
            for tc in tc_raw:
                if not isinstance(tc, dict):
                    continue
                if tc.get("type") == "function" and isinstance(tc.get("function"), dict):
                    fn = tc["function"]
                    tid = str(tc.get("id") or "").strip() or f"call_{uuid4().hex[:12]}"
                    name = str(fn.get("name") or "")
                    arg_raw = fn.get("arguments", "{}")
                    if isinstance(arg_raw, dict):
                        inp: dict[str, Any] = dict(arg_raw)
                    else:
                        try:
                            inp = json.loads(arg_raw) if arg_raw else {}
                        except json.JSONDecodeError:
                            inp = {}
                    blocks.append({"type": "tool_use", "id": tid, "name": name, "input": inp})
                elif "name" in tc:
                    tid = str(tc.get("id") or "").strip() or f"call_{uuid4().hex[:12]}"
                    args = tc.get("args", {})
                    inp = args if isinstance(args, dict) else {}
                    blocks.append({"type": "tool_use", "id": tid, "name": str(tc.get("name")), "input": inp})
            if blocks:
                out.append({"role": "assistant", "content": blocks})
        elif isinstance(m, ToolMessage):
            tid = str(m.tool_call_id or "").strip() or f"call_{uuid4().hex[:12]}"
            pending_tool_results.append(
                {"type": "tool_result", "tool_use_id": tid, "content": _content_str(m.content)}
            )
        else:
            flush_tools()
            out.append({"role": "user", "content": _content_str(getattr(m, "content", ""))})

    flush_tools()
    return out


def _merge_system_prompt(working_messages: list) -> tuple[str, list]:
    sys_parts: list[str] = []
    rest: list = []
    for m in working_messages:
        if isinstance(m, SystemMessage):
            sys_parts.append(_content_str(m.content))
        else:
            rest.append(m)
    return "\n\n".join(sys_parts), rest


async def _stream_one_turn_anthropic(
    *,
    client,
    model: str,
    system: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
    session_id,
    run_id,
    forced_tool_name: str | None = None,
) -> tuple[str, list[_PendingToolCall], tuple[int, int]]:
    text_buf = ""
    prompt_tokens = completion_tokens = 0
    tool_calls_by_index: dict[int, _PendingToolCall] = {}

    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": _ANTHROPIC_MAX_TOKENS,
        "system": system,
        "messages": messages,
        "temperature": 0.3,
    }
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = anthropic_tool_choice(forced_tool_name)

    async with client.messages.stream(**kwargs) as stream:
        async for event in stream:
            et = getattr(event, "type", None)
            if et == "content_block_start":
                cb = event.content_block
                cbt = getattr(cb, "type", None)
                if cbt == "tool_use":
                    idx = event.index
                    pc = tool_calls_by_index.setdefault(idx, _PendingToolCall())
                    pc.id = str(getattr(cb, "id", "") or "") or f"call_{uuid4().hex[:12]}"
                    pc.name = str(getattr(cb, "name", "") or "")
                    if getattr(cb, "input", None):
                        try:
                            pc.args_buf = json.dumps(cb.input, ensure_ascii=False)
                        except (TypeError, ValueError):
                            pc.args_buf = str(cb.input)
            elif et == "content_block_delta":
                delta = event.delta
                dt = getattr(delta, "type", None)
                if dt == "text_delta":
                    piece = getattr(delta, "text", "") or ""
                    if piece:
                        text_buf += piece
                        await emit(
                            MessageDeltaEvent(session_id=session_id, run_id=run_id, text=piece)
                        )
                elif dt == "input_json_delta":
                    idx = event.index
                    pc = tool_calls_by_index.setdefault(idx, _PendingToolCall())
                    partial = getattr(delta, "partial_json", "") or ""
                    pc.args_buf += partial
            elif et == "message_delta" and getattr(event, "usage", None):
                u = event.usage
                prompt_tokens = getattr(u, "input_tokens", None) or prompt_tokens
                completion_tokens = getattr(u, "output_tokens", None) or completion_tokens

    pending: list[_PendingToolCall] = []
    for idx in sorted(tool_calls_by_index):
        pc = tool_calls_by_index[idx]
        if not pc.id:
            pc.id = f"call_{uuid4().hex[:12]}"
        if pc.name:
            pending.append(pc)

    return text_buf, pending, (prompt_tokens, completion_tokens)


async def execute_anthropic_node(state: SessionState) -> SessionState:
    settings = get_settings()
    session_id = state["session_id"]
    run_id = state.get("run_id")
    exec_alias = (state.get("executor_model") or settings.agent_default_executor).strip()
    coder_alias = (state.get("coder_model") or settings.agent_default_coder).strip()
    sandbox_id = state.get("sandbox_id") or str(session_id)

    client = get_async_anthropic()
    manifests = list(tool_manifest_cache().values())
    manifest_by_name = {m.name: m for m in manifests}
    tools_schema = manifests_to_anthropic_tools(manifests) if manifests else None

    user_message = state.get("user_message") or ""
    memories = await search_memories(
        user_message,
        session_id=str(session_id),
        user_id=state.get("user_id"),
    )
    memory_block = format_memories(memories)
    extra_context = f"### 用户长期记忆（来自 mem0）\n{memory_block}" if memory_block else None

    system_prompt = build_system_prompt(
        session_id=str(session_id),
        user_id=state.get("user_id"),
        manifests=manifests,
        extra_context=extra_context,
    )

    working_messages: list = list(state.get("messages") or [])
    if not any(isinstance(m, SystemMessage) for m in working_messages):
        working_messages = [SystemMessage(content=system_prompt)] + working_messages

    system_merged, body_chain = _merge_system_prompt(working_messages)
    if not system_merged.strip():
        system_merged = system_prompt

    log.info(
        "graph.execute_anthropic.start",
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

    plan = await mark_progress(plan, session_id=session_id, run_id=run_id, start_next=True)

    try:
        while tool_turns < max_turns:
            working_messages, did_compact, summary = await maybe_compact(
                working_messages,
                session_id=session_id,
                run_id=run_id,
                compact_model=state.get("compact_model"),
                longctx_model=state.get("longctx_model"),
            )
            if did_compact and summary:
                compact_memory = summary

            system_merged, body_chain = _merge_system_prompt(working_messages)
            if not system_merged.strip():
                system_merged = system_prompt
            api_messages = _langchain_body_to_anthropic_messages(body_chain)

            turn_model = pick_executor_turn_model(
                working_messages=working_messages,
                manifest_by_name=manifest_by_name,
                executor_model=exec_alias,
                coder_model=coder_alias,
            )
            turn_text, pending_calls, usage = await _stream_one_turn_anthropic(
                client=client,
                model=turn_model,
                system=system_merged,
                messages=api_messages,
                tools=tools_schema,
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

            assistant_kwargs: dict[str, Any] = {"content": turn_text}
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
                        "graph.execute_anthropic.delivery_proof_missing",
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
            log.warning("graph.execute_anthropic.max_turns", session_id=str(session_id), turns=tool_turns)
            final_text = "（已达到最大工具调用轮数上限，未能完成任务。请尝试拆小或直接提问。）"
            plan = await mark_progress(
                plan, session_id=session_id, run_id=run_id, fail_current=True
            )
    except Exception as exc:  # noqa: BLE001
        log.exception("graph.execute_anthropic.failed", error=str(exc))
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
        "graph.execute_anthropic.done",
        session_id=str(session_id),
        turns=tool_turns,
        chars=len(final_text),
        in_tokens=prompt_tokens_total,
        out_tokens=completion_tokens_total,
    )

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
