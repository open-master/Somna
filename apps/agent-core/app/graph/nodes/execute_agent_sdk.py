"""execute node — 模式二：LangGraph 负责会话状态 / 校验 / 对外事件，Claude Agent SDK 负责 Agent+Tool 循环。

- 模式一（本文件不涉及）：OpenAI Chat Completions 自研 loop，见 `execute.py`。
- 本路径用 `claude_agent_sdk.query()` + 进程内 MCP（`create_sdk_mcp_server`），工具实现仍走
  既有 MCP Hub HTTP（与模式一共 `_invoke_tool_with_events`），别名与 `ANTHROPIC_*` 网关约定同 `client.py`。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    SdkMcpTool,
    StreamEvent,
    TextBlock,
    ThinkingBlock,
    ToolUseBlock,
    create_sdk_mcp_server,
    query,
)
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from somna_events import MessageDeltaEvent

from app.config import get_settings
from app.events.emitter import emit
from app.graph.autonomy_policy import delivery_validation_policy, effective_autonomy_level
from app.graph.compact import maybe_compact
from app.graph.model_policy import pick_executor_turn_model
from app.graph.nodes.execute import (
    _completion_retry_message,
    _compose_executor_extra_context,
    _content_str,
    _delivery_recovery_tool_name,
    _emit_skill_debug_event,
    _ExecutionProof,
    _has_execution_progress,
    _invoke_tool_with_events,
    _merge_proof,
    _PendingToolCall,
    _proof_from_execution_summary,
    _reject_out_of_order_tool,
    _render_tool_content,
    _replace_active_todo_instruction,
    _retry_failed_tool_if_needed,
    _route_or_reuse_skills,
    _stop_blocked_reason,
    _summarize_execution,
    _with_fresh_system_prompt,
    active_todo_allows_tool,
    effective_mcp_tool_models_map,
)
from app.graph.nodes.plan import advance_with_proof, advance_with_response_text, mark_progress
from app.graph.run_artifacts import append_executor_progress_snapshot, sync_plan_artifact
from app.graph.state import SessionState
from app.graph.tool_policy import manifests_allowed_by_task_frame
from app.graph.user_turn import executor_messages_for_current_turn, last_human_turn_text
from app.llm.client import anthropic_subprocess_env
from app.logging_setup import get_logger
from app.memory import format_memories, search_memories
from app.prompts.loader import build_system_prompt
from app.services.billing import emit_model_usage
from app.services.skill_router import selected_skills_payload
from app.tools.client import ToolManifest, get_client
from app.tools.schema import tool_manifest_cache

log = get_logger(__name__)

_SOMNA_MCP_SERVER_NAME = "somna"


@dataclass
class _SomnaBridge:
    sandbox_id: str
    session_id: str
    run_id: str | None
    working_messages: list
    manifest_by_name: dict[str, ToolManifest]
    proof: _ExecutionProof
    plan: dict[str, Any] | None
    artifact_state: dict[str, Any]
    reflection_count: int = 0
    mcp_tool_models: dict[str, str] | None = None
    billing_enabled: bool = False
    tool_round: int = 0
    max_tool_turns: int = 40

    async def run_tool(self, tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
        """MCP 工具 handler：桥接 MCP Hub + 事件 + proof/plan（与模式一同一套）。"""
        if self.proof.scheduler_rejections:
            return {"content": [{"type": "text", "text": "正在重新规划，请等待调度恢复。"}], "is_error": True}
        if self.tool_round >= self.max_tool_turns:
            return {
                "content": [{"type": "text", "text": "全局工具调用预算已用尽，不能继续调用工具。"}],
                "is_error": True,
            }
        self.tool_round += 1
        operation_scope = f"{self.reflection_count}:{self.tool_round}:0"
        pc = _PendingToolCall()
        pc.id = f"call_{uuid4().hex[:12]}"
        pc.name = tool_name
        pc.args_buf = json.dumps(args, ensure_ascii=False)
        manifest = self.manifest_by_name.get(tool_name)

        if not active_todo_allows_tool(
            self.plan,
            tool_name,
            available_tool_names=set(self.manifest_by_name),
        ):
            message = (
                f"[scheduler_todo_mismatch] 严格顺序调度器已阻止 {tool_name}："
                "该工具不属于当前 TODO。请完成当前步骤或结束本轮请求重规划；"
                "这是内部冲突，不能让用户授权绕过。"
            )
            await _reject_out_of_order_tool(
                session_id=self.session_id,
                run_id=self.run_id,
                working_messages=self.working_messages,
                event_id=pc.id,
                tool_name=tool_name,
                args=args,
            )
            self.proof = _merge_proof(
                self.proof,
                _ExecutionProof(
                    scheduler_rejections=1,
                    failure_notes=[f"scheduler_todo_mismatch:{tool_name}"],
                ),
            )
            return {
                "content": [{"type": "text", "text": message}],
                "is_error": True,
            }

        result, delta = await _invoke_tool_with_events(
            mcp=get_client(),
            tool_name=tool_name,
            args=args,
            event_id=pc.id,
            sandbox_id=self.sandbox_id,
            session_id=self.session_id,
            run_id=self.run_id,
            working_messages=self.working_messages,
            manifest=manifest,
            mcp_tool_models=self.mcp_tool_models,
            billing_enabled=self.billing_enabled,
            operation_index=operation_scope,
        )
        proof_acc = delta
        result, extra = await _retry_failed_tool_if_needed(
            mcp=get_client(),
            tool_name=tool_name,
            args=args,
            result=result,
            event_id=pc.id,
            sandbox_id=self.sandbox_id,
            session_id=self.session_id,
            run_id=self.run_id,
            working_messages=self.working_messages,
            manifest_by_name=self.manifest_by_name,
            mcp_tool_models=self.mcp_tool_models,
            billing_enabled=self.billing_enabled,
            operation_prefix=operation_scope,
        )
        proof_acc = _merge_proof(proof_acc, extra)

        previous_plan = self.plan
        self.plan = await advance_with_proof(
            self.plan,
            session_id=self.session_id,
            run_id=self.run_id,
            proof=proof_acc,
        )
        from app.graph.nodes.execute import _track_task_recovery

        _track_task_recovery(proof_acc, previous_plan, self.plan)
        self.proof = _merge_proof(self.proof, proof_acc)
        _replace_active_todo_instruction(self.working_messages, self.plan)
        await sync_plan_artifact(self.artifact_state, self.plan)
        await append_executor_progress_snapshot(
            self.artifact_state,
            sandbox_id=self.sandbox_id,
            run_id=self.run_id,
            tool_turns=self.tool_round,
            ok_calls=self.proof.successful_tool_calls,
            n_written=len(self.proof.written_paths),
            n_verified=len(self.proof.verified_paths),
            note="after_tools",
        )
        out: dict[str, Any] = {
            "content": [{"type": "text", "text": _render_tool_content(result)}],
        }
        if not result.ok:
            out["is_error"] = True
        return out


def _normalize_json_schema(schema: dict[str, Any] | None) -> dict[str, Any]:
    s = dict(schema or {})
    if s.get("type") != "object":
        return {"type": "object", "properties": {}}
    s.setdefault("properties", {})
    return s


def _somna_sdk_tools(bridge: _SomnaBridge, manifests: list[ToolManifest]) -> list[SdkMcpTool[Any]]:
    tools: list[SdkMcpTool[Any]] = []

    def _handler_for(name: str):
        async def _handler(args: dict[str, Any]) -> dict[str, Any]:
            return await bridge.run_tool(name, args)

        return _handler

    for m in manifests:
        tools.append(
            SdkMcpTool(
                name=m.name,
                description=(m.description or "")[:4096],
                input_schema=_normalize_json_schema(m.input_schema),
                handler=_handler_for(m.name),
            )
        )
    return tools


def _merge_system_prompt(working_messages: list) -> tuple[str, list]:
    sys_parts: list[str] = []
    rest: list = []
    for m in working_messages:
        if isinstance(m, SystemMessage):
            sys_parts.append(_content_str(m.content))
        else:
            rest.append(m)
    return "\n\n".join(sys_parts), rest


def _sdk_progress_snapshot(proof: _ExecutionProof | None) -> str:
    if proof is None:
        return ""
    written = sorted(str(p) for p in (proof.written_paths or set()) if str(p).strip())[:12]
    return (
        "【本轮已确认进度】\n"
        f"已成功工具调用：{int(proof.successful_tool_calls or 0)}\n"
        f"已写入路径：{', '.join(written) if written else '（无）'}"
    )


def _body_chain_to_user_prompt(
    body_chain: list,
    *,
    user_message: str,
    retry_instruction: str | None,
    proof: _ExecutionProof | None = None,
) -> str:
    """Build a compact SDK user prompt.

    Delivery retries must not re-dump the full tool transcript: each `query()`
    already starts a fresh Agent loop, and repeating ToolMessage blobs burns
    the global token budget while encouraging more search-only turns.
    """
    parts: list[str] = []
    tail = (user_message or "").strip()
    if retry_instruction:
        parts.append(retry_instruction)
        snapshot = _sdk_progress_snapshot(proof)
        if snapshot:
            parts.append(snapshot)
        if tail:
            parts.append(f"【当前用户请求】\n{tail}")
        return "\n\n".join(parts)

    last_human = ""
    last_assistant = ""
    for m in body_chain:
        if isinstance(m, HumanMessage):
            last_human = _content_str(m.content)
        elif isinstance(m, AIMessage):
            text = _content_str(m.content).strip()
            if text:
                last_assistant = text
    if last_human:
        parts.append(f"【用户】\n{last_human[:8000]}")
    if last_assistant:
        parts.append(f"【助手上一轮】\n{last_assistant[:2000]}")
    if tail and tail not in last_human:
        parts.append(f"【当前用户请求】\n{tail}")
    return "\n\n".join(parts)


def _sdk_delivery_retry_instruction(reason: str, *, attempt: int) -> str:
    """比模式一更直白：要求下一轮必须出现写沙盒证据（filesystem / shell）。"""
    core = _completion_retry_message(reason)
    extra = (
        "\n\n【续跑要求 — 模式二】"
        "若上文已有 search 结果，禁止再只用 search 或纯文字收束。"
        "你必须在本回合至少调用一次 **`filesystem`（`write`/`append`）** 或 **`shell` 重定向**"
        "把工作区文件写入沙盒（例如 `jobs_gates.txt`、`wordcloud.py`、`.png` 等），"
        "必要时再用 `filesystem.stat` / `shell` 验证文件存在。"
        f"\n（当前为第 {attempt} 次续跑，未完成文件落盘则视为失败。）"
    )
    return core + extra


def _stream_event_text(ev: StreamEvent) -> str:
    raw = ev.event
    if not isinstance(raw, dict):
        return ""
    if raw.get("type") == "content_block_delta":
        delta = raw.get("delta") or {}
        if delta.get("type") == "text_delta":
            return str(delta.get("text") or "")
    return ""


def _assistant_to_langchain(message: AssistantMessage) -> AIMessage:
    text_chunks: list[str] = []
    tool_wire: list[dict[str, Any]] = []
    for block in message.content:
        if isinstance(block, TextBlock):
            text_chunks.append(block.text)
        elif isinstance(block, ThinkingBlock):
            continue
        elif isinstance(block, ToolUseBlock):
            tool_wire.append(
                {
                    "id": block.id or f"call_{uuid4().hex[:12]}",
                    "type": "function",
                    "function": {
                        "name": block.name,
                        "arguments": json.dumps(block.input, ensure_ascii=False) if block.input else "{}",
                    },
                }
            )
    kwargs: dict[str, Any] = {"content": "".join(text_chunks)}
    if tool_wire:
        kwargs["additional_kwargs"] = {"tool_calls": tool_wire}
    return AIMessage(**kwargs)


async def execute_agent_sdk_node(state: SessionState) -> SessionState:
    settings = get_settings()
    session_id = state["session_id"]
    run_id = state.get("run_id")
    exec_alias = (state.get("executor_model") or settings.agent_default_executor).strip()
    coder_alias = (state.get("coder_model") or settings.agent_default_coder).strip()
    sandbox_id = state.get("sandbox_id") or str(session_id)
    user_message = last_human_turn_text(state)
    _tf = state.get("task_frame") if isinstance(state.get("task_frame"), dict) else None
    _eff_auto = effective_autonomy_level(_tf)
    _sdk_max_delivery_rounds = delivery_validation_policy(_eff_auto).max_sdk_delivery_rounds

    manifests = manifests_allowed_by_task_frame(
        list(tool_manifest_cache().values()),
        state.get("task_frame") if isinstance(state.get("task_frame"), dict) else None,
    )
    manifest_by_name = {m.name: m for m in manifests}
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

    system_prompt = build_system_prompt(
        session_id=str(session_id),
        user_id=state.get("user_id"),
        manifests=manifests,
        extra_context=extra_context,
        enabled_skill_names=[item.name for item in skill_route.selected],
    )

    working_messages = executor_messages_for_current_turn(state)
    working_messages = _with_fresh_system_prompt(working_messages, system_prompt)

    proof = _proof_from_execution_summary(state.get("execution_summary"))
    mcp_maps = effective_mcp_tool_models_map(state)
    artifact_state: dict[str, Any] = {
        "session_id": session_id,
        "sandbox_id": sandbox_id,
        "run_id": run_id,
    }
    bridge = _SomnaBridge(
        sandbox_id=sandbox_id,
        session_id=session_id,
        run_id=run_id,
        working_messages=working_messages,
        manifest_by_name=manifest_by_name,
        proof=proof,
        plan=state.get("plan"),
        artifact_state=artifact_state,
        reflection_count=int(state.get("reflection_count") or 0),
        mcp_tool_models=mcp_maps,
        billing_enabled=bool(state.get("user_id")),
        tool_round=int(state.get("tool_turns") or 0),
        max_tool_turns=max(1, int(settings.agent_max_turns)),
    )
    somna = create_sdk_mcp_server(
        name=_SOMNA_MCP_SERVER_NAME,
        tools=_somna_sdk_tools(bridge, manifests),
    )

    compact_memory = state.get("compact_memory")
    plan = bridge.plan
    plan = await mark_progress(
        plan,
        session_id=session_id,
        run_id=run_id,
        start_next=True,
        retry_failed=True,
    )
    bridge.plan = plan
    _replace_active_todo_instruction(working_messages, plan)
    await sync_plan_artifact(artifact_state, plan)

    log.info(
        "graph.execute_agent_sdk.start",
        session_id=str(session_id),
        executor_model=exec_alias,
        coder_model=coder_alias,
        skill_model=(state.get("skill_model") or settings.agent_default_skill).strip(),
        selected_skills=state.get("selected_skills") or [],
        n_msgs=len(working_messages),
        tools=len(manifests),
        effective_autonomy=_eff_auto,
        max_sdk_delivery_rounds=_sdk_max_delivery_rounds,
    )

    prompt_tokens_total = completion_tokens_total = 0
    total_agent_turns = int(state.get("total_agent_turns") or 0)
    total_execution_tokens = int(state.get("total_execution_tokens") or 0)
    max_total_turns = max(1, int(getattr(settings, "agent_max_total_turns", 80)))
    max_total_tokens = max(1, int(getattr(settings, "agent_max_total_tokens", 500000)))
    final_text = ""
    finish_validation_failures = 0
    retry_instruction: str | None = None
    delivery_reason: str | None = None

    try:
        t_start = time.perf_counter()
        while finish_validation_failures < _sdk_max_delivery_rounds:
            remaining_agent_turns = max_total_turns - total_agent_turns
            if remaining_agent_turns <= 0 or total_execution_tokens >= max_total_tokens:
                delivery_reason = "已达到全局 Agent turn 或 executor token 预算上限"
                plan = await mark_progress(
                    bridge.plan,
                    session_id=session_id,
                    run_id=run_id,
                    fail_current=True,
                    failure_reason=delivery_reason,
                )
                bridge.plan = plan
                break
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
            bridge.working_messages = working_messages

            system_merged, body_chain = _merge_system_prompt(working_messages)
            if not system_merged.strip():
                system_merged = system_prompt

            turn_model = pick_executor_turn_model(
                working_messages=working_messages,
                manifest_by_name=manifest_by_name,
                executor_model=exec_alias,
                coder_model=coder_alias,
            )
            user_prompt = _body_chain_to_user_prompt(
                body_chain,
                user_message=user_message,
                retry_instruction=retry_instruction,
                proof=bridge.proof,
            )

            options = ClaudeAgentOptions(
                tools=[],
                mcp_servers={_SOMNA_MCP_SERVER_NAME: somna},
                allowed_tools=[m.name for m in manifests],
                permission_mode="bypassPermissions",
                model=turn_model,
                max_turns=min(max(1, int(settings.agent_max_turns)), remaining_agent_turns),
                env=dict(anthropic_subprocess_env()),
                system_prompt=system_merged,
                include_partial_messages=True,
            )

            assistant_text_buf: list[str] = []
            last_result: ResultMessage | None = None

            from contextlib import aclosing

            async with aclosing(query(prompt=user_prompt, options=options)) as responses:
                async for message in responses:
                    if isinstance(message, StreamEvent):
                        piece = _stream_event_text(message)
                        if piece:
                            assistant_text_buf.append(piece)
                            await emit(MessageDeltaEvent(session_id=session_id, run_id=run_id, text=piece))
                    elif isinstance(message, AssistantMessage):
                        working_messages.append(_assistant_to_langchain(message))
                        if message.error:
                            log.warning(
                                "graph.execute_agent_sdk.assistant_error",
                                session_id=str(session_id),
                                error=message.error,
                            )
                        if not assistant_text_buf:
                            for block in message.content:
                                if isinstance(block, TextBlock) and block.text:
                                    assistant_text_buf.append(block.text)
                                    await emit(
                                        MessageDeltaEvent(
                                            session_id=session_id, run_id=run_id, text=block.text
                                        )
                                    )
                    elif isinstance(message, ResultMessage):
                        last_result = message
                        u = message.usage or {}
                        if isinstance(u, dict):
                            pi = int(u.get("input_tokens") or u.get("prompt_tokens") or 0)
                            co = int(u.get("output_tokens") or u.get("completion_tokens") or 0)
                            if pi or co:
                                prompt_tokens_total += pi
                                completion_tokens_total += co
                                total_execution_tokens += max(0, pi) + max(0, co)
                                sdk_cost = float(getattr(message, "total_cost_usd", 0.0) or 0.0)
                                await emit_model_usage(
                                    session_id=session_id,
                                    run_id=run_id,
                                    usage_key=(
                                        f"{run_id}:execute_sdk:{int(state.get('reflection_count') or 0)}:"
                                        f"{finish_validation_failures}:{int(message.num_turns or 0)}"
                                    ),
                                    phase="execute_agent_sdk",
                                    model=turn_model,
                                    input_tokens=pi,
                                    output_tokens=co,
                                    cost_usd=sdk_cost,
                                )
                    if bridge.proof.scheduler_rejections:
                        break

            final_text = "".join(assistant_text_buf).strip()
            if not final_text and last_result and last_result.result:
                final_text = str(last_result.result).strip()
            proof = bridge.proof
            plan = bridge.plan
            if last_result:
                total_agent_turns += max(0, int(last_result.num_turns or 0))
            elif proof.scheduler_rejections:
                total_agent_turns += 1
            if proof.scheduler_rejections:
                break

            plan = await advance_with_response_text(
                plan,
                session_id=session_id,
                run_id=run_id,
                response_text=final_text,
            )
            bridge.plan = plan
            await sync_plan_artifact(artifact_state, plan)
            _replace_active_todo_instruction(working_messages, plan)

            delivery_reason = await _stop_blocked_reason(
                user_message=user_message,
                plan=plan,
                proof=proof,
                task_frame=state.get("task_frame"),
                sandbox_id=sandbox_id,
            )
            if not delivery_reason:
                break

            finish_validation_failures += 1
            lr = last_result
            log.warning(
                "graph.execute_agent_sdk.delivery_proof_missing",
                session_id=str(session_id),
                run_id=run_id,
                attempt=finish_validation_failures,
                reason=delivery_reason,
                sdk_num_turns=getattr(lr, "num_turns", None),
                sdk_stop_reason=getattr(lr, "stop_reason", None),
                sdk_is_error=getattr(lr, "is_error", None),
            )
            if finish_validation_failures >= _sdk_max_delivery_rounds:
                plan = await mark_progress(
                    plan,
                    session_id=session_id,
                    run_id=run_id,
                    fail_current=True,
                    failure_reason=delivery_reason,
                )
                await sync_plan_artifact(artifact_state, plan)
                await append_executor_progress_snapshot(
                    artifact_state,
                    sandbox_id=sandbox_id,
                    run_id=run_id,
                    tool_turns=bridge.tool_round,
                    ok_calls=proof.successful_tool_calls,
                    n_written=len(proof.written_paths),
                    n_verified=len(proof.verified_paths),
                    note=f"delivery_validation_failed:{delivery_reason[:80]}",
                )
                new_messages = [m for m in working_messages if not isinstance(m, SystemMessage)]
                return {
                    "assistant_text": final_text,
                    "messages": new_messages,
                    "compact_memory": compact_memory,
                    "tool_turns": bridge.tool_round,
                    "total_agent_turns": total_agent_turns,
                    "total_execution_tokens": total_execution_tokens,
                    "plan": plan,
                    "execution_summary": _summarize_execution(proof, delivery_missing_reason=delivery_reason),
                    "finished": True,
                }

            retry_instruction = _sdk_delivery_retry_instruction(
                delivery_reason, attempt=finish_validation_failures + 1
            )
            forced = _delivery_recovery_tool_name(manifests)
            if forced:
                retry_instruction += f"\n\n优先使用工具：`{forced}`。"
            # 下一轮 query：由 SDK 再次驱动 tool loop；本侧 LangGraph 只做提示与状态衔接。

    except Exception as exc:  # noqa: BLE001
        log.exception("graph.execute_agent_sdk.failed", error=str(exc))
        recoverable = _has_execution_progress(bridge.proof)
        plan = bridge.plan
        if not recoverable:
            plan = await mark_progress(
                plan,
                session_id=session_id,
                run_id=run_id,
                fail_current=True,
                failure_reason=f"执行异常：{str(exc)[:180]}",
            )
        await sync_plan_artifact(artifact_state, plan)
        await append_executor_progress_snapshot(
            artifact_state,
            sandbox_id=sandbox_id,
            run_id=run_id,
            tool_turns=bridge.tool_round,
            ok_calls=bridge.proof.successful_tool_calls,
            n_written=len(bridge.proof.written_paths),
            n_verified=len(bridge.proof.verified_paths),
            note=f"exception:{str(exc)[:120]}",
        )
        payload: dict[str, Any] = {
            "finished": True,
            "assistant_text": final_text,
            "messages": [
                message for message in bridge.working_messages if not isinstance(message, SystemMessage)
            ],
            "compact_memory": compact_memory,
            "tool_turns": bridge.tool_round,
            "total_agent_turns": total_agent_turns,
            "total_execution_tokens": total_execution_tokens,
            "plan": plan,
            "execution_summary": _summarize_execution(bridge.proof, execute_exception=str(exc)[:240]),
        }
        if not recoverable:
            payload["error"] = str(exc)
        return payload

    log.info(
        "graph.execute_agent_sdk.done",
        session_id=str(session_id),
        turns=bridge.tool_round,
        chars=len(final_text),
        in_tokens=prompt_tokens_total,
        out_tokens=completion_tokens_total,
        elapsed_s=round(time.perf_counter() - t_start, 3),
    )

    await append_executor_progress_snapshot(
        artifact_state,
        sandbox_id=sandbox_id,
        run_id=run_id,
        tool_turns=bridge.tool_round,
        ok_calls=bridge.proof.successful_tool_calls,
        n_written=len(bridge.proof.written_paths),
        n_verified=len(bridge.proof.verified_paths),
        note="execute_done",
    )

    new_messages = [m for m in working_messages if not isinstance(m, SystemMessage)]
    plan = bridge.plan
    proof = bridge.proof

    return {
        "assistant_text": final_text,
        "messages": new_messages,
        "compact_memory": compact_memory,
        "tool_turns": bridge.tool_round,
        "total_agent_turns": total_agent_turns,
        "total_execution_tokens": total_execution_tokens,
        "plan": plan,
        "execution_summary": _summarize_execution(proof),
        "finished": True,
    }
