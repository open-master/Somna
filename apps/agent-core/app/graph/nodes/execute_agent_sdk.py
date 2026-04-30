"""execute node — 模式二：LangGraph 负责会话状态 / 校验 / 对外事件，Claude Agent SDK 负责 Agent+Tool 循环。

- 模式一（本文件不涉及）：OpenAI Chat Completions 自研 loop，见 `execute.py`。
- 本路径用 `claude_agent_sdk.query()` + 进程内 MCP（`create_sdk_mcp_server`），工具实现仍走
  既有 MCP Hub HTTP（与模式一共 `_invoke_tool_with_events`），别名与 `ANTHROPIC_*` 网关约定同 `client.py`。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
import time
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
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from somna_events import MessageDeltaEvent, TokenUsageEvent

from app.config import get_settings
from app.events.emitter import emit
from app.graph.compact import maybe_compact
from app.graph.model_policy import pick_executor_turn_model
from app.graph.nodes.plan import advance_with_proof, mark_progress
from app.graph.run_artifacts import append_executor_progress_snapshot, sync_plan_artifact
from app.graph.state import SessionState
from app.llm.client import anthropic_subprocess_env
from app.logging_setup import get_logger
from app.memory import format_memories, search_memories
from app.prompts.loader import build_system_prompt
from app.tools.client import get_client, ToolManifest
from app.tools.schema import tool_manifest_cache

from app.graph.nodes.execute import (
    _ExecutionProof,
    _PendingToolCall,
    _compose_executor_extra_context,
    _completion_retry_message,
    _content_str,
    _delivery_recovery_tool_name,
    _detect_shell_recovery,
    _invoke_tool_with_events,
    _merge_proof,
    _missing_delivery_reason,
    _proof_from_execution_summary,
    _render_tool_content,
    _summarize_execution,
)

log = get_logger(__name__)

_SOMNA_MCP_SERVER_NAME = "somna"

# 模式二每次 query() 为独立子进程回合；交付校验失败时多给几轮，避免仅 search 就结束。
_SDK_DELIVERY_MAX_ROUNDS = 10


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
    tool_round: int = 0

    async def run_tool(self, tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
        """MCP 工具 handler：桥接 MCP Hub + 事件 + proof/plan（与模式一同一套）。"""
        self.tool_round += 1
        pc = _PendingToolCall()
        pc.id = f"call_{uuid4().hex[:12]}"
        pc.name = tool_name
        pc.args_buf = json.dumps(args, ensure_ascii=False)
        manifest = self.manifest_by_name.get(tool_name)

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
        )
        proof_acc = delta

        recovery = _detect_shell_recovery(args=args, result=result)
        if recovery is not None:
            log.info(
                "graph.execute_agent_sdk.shell_auto_recover",
                session_id=str(self.session_id),
                run_id=self.run_id,
                reason=recovery.reason,
                install_cmd=recovery.install_cmd,
            )
            install_result, install_proof = await _invoke_tool_with_events(
                mcp=get_client(),
                tool_name="shell",
                args={"cmd": recovery.install_cmd, "cwd": args.get("cwd")},
                event_id=f"{pc.id}_recover_install",
                sandbox_id=self.sandbox_id,
                session_id=self.session_id,
                run_id=self.run_id,
                working_messages=self.working_messages,
                manifest=self.manifest_by_name.get("shell"),
            )
            proof_acc = _merge_proof(proof_acc, install_proof)
            if install_result.ok:
                result, retry_proof = await _invoke_tool_with_events(
                    mcp=get_client(),
                    tool_name=tool_name,
                    args=args,
                    event_id=f"{pc.id}_recover_retry",
                    sandbox_id=self.sandbox_id,
                    session_id=self.session_id,
                    run_id=self.run_id,
                    working_messages=self.working_messages,
                    manifest=manifest,
                )
                proof_acc = _merge_proof(proof_acc, retry_proof)

        self.proof = _merge_proof(self.proof, proof_acc)
        self.plan = await advance_with_proof(
            self.plan,
            session_id=self.session_id,
            run_id=self.run_id,
            proof=proof_acc,
        )
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


def _body_chain_to_user_prompt(
    body_chain: list,
    *,
    user_message: str,
    retry_instruction: str | None,
) -> str:
    parts: list[str] = []
    if retry_instruction:
        parts.append(retry_instruction)
    for m in body_chain:
        if isinstance(m, HumanMessage):
            parts.append(f"【用户】\n{_content_str(m.content)}")
        elif isinstance(m, AIMessage):
            parts.append(f"【助手】\n{_content_str(m.content)}")
        elif isinstance(m, ToolMessage):
            parts.append(f"【工具 {getattr(m, 'name', '') or '?'} 输出】\n{_content_str(m.content)[:4000]}")
        else:
            parts.append(f"【其它】\n{_content_str(getattr(m, 'content', ''))}")
    tail = (user_message or "").strip()
    if tail:
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
    user_message = state.get("user_message") or ""

    manifests = list(tool_manifest_cache().values())
    manifest_by_name = {m.name: m for m in manifests}
    memories = await search_memories(
        user_message,
        session_id=str(session_id),
        user_id=state.get("user_id"),
    )
    memory_block = format_memories(memories)
    extra_context = _compose_executor_extra_context(memory_block, state.get("task_frame"))

    system_prompt = build_system_prompt(
        session_id=str(session_id),
        user_id=state.get("user_id"),
        manifests=manifests,
        extra_context=extra_context,
    )

    working_messages: list = list(state.get("messages") or [])
    if not any(isinstance(m, SystemMessage) for m in working_messages):
        working_messages = [SystemMessage(content=system_prompt)] + working_messages

    proof = _proof_from_execution_summary(state.get("execution_summary"))
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
    )
    somna = create_sdk_mcp_server(
        name=_SOMNA_MCP_SERVER_NAME,
        tools=_somna_sdk_tools(bridge, manifests),
    )

    compact_memory = state.get("compact_memory")
    plan = bridge.plan
    plan = await mark_progress(plan, session_id=session_id, run_id=run_id, start_next=True)
    bridge.plan = plan
    await sync_plan_artifact(artifact_state, plan)

    log.info(
        "graph.execute_agent_sdk.start",
        session_id=str(session_id),
        executor_model=exec_alias,
        coder_model=coder_alias,
        n_msgs=len(working_messages),
        tools=len(manifests),
    )

    prompt_tokens_total = completion_tokens_total = 0
    tool_turns = int(state.get("tool_turns") or 0)
    final_text = ""
    finish_validation_failures = 0
    retry_instruction: str | None = None
    delivery_reason: str | None = None

    try:
        t_start = time.perf_counter()
        while finish_validation_failures < _SDK_DELIVERY_MAX_ROUNDS:
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
            )

            options = ClaudeAgentOptions(
                tools=[],
                mcp_servers={_SOMNA_MCP_SERVER_NAME: somna},
                allowed_tools=[m.name for m in manifests],
                permission_mode="bypassPermissions",
                model=turn_model,
                max_turns=settings.agent_max_turns,
                env=dict(anthropic_subprocess_env()),
                system_prompt=system_merged,
                include_partial_messages=True,
            )

            assistant_text_buf: list[str] = []
            last_result: ResultMessage | None = None

            async for message in query(prompt=user_prompt, options=options):
                if isinstance(message, StreamEvent):
                    piece = _stream_event_text(message)
                    if piece:
                        assistant_text_buf.append(piece)
                        await emit(
                            MessageDeltaEvent(session_id=session_id, run_id=run_id, text=piece)
                        )
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
                            await emit(
                                TokenUsageEvent(
                                    session_id=session_id,
                                    run_id=run_id,
                                    model=turn_model,
                                    input=pi,
                                    output=co,
                                    cost_usd=0.0,
                                )
                            )

            final_text = "".join(assistant_text_buf).strip()
            if not final_text and last_result and last_result.result:
                final_text = str(last_result.result).strip()

            proof = bridge.proof
            plan = bridge.plan
            if last_result:
                tool_turns = max(tool_turns, int(last_result.num_turns or 0))

            delivery_reason = _missing_delivery_reason(
                user_message=user_message,
                plan=plan,
                proof=proof,
                task_frame=state.get("task_frame"),
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
            if finish_validation_failures >= _SDK_DELIVERY_MAX_ROUNDS:
                plan = await mark_progress(plan, session_id=session_id, run_id=run_id, fail_current=True)
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
                    "tool_turns": tool_turns,
                    "plan": plan,
                    "execution_summary": _summarize_execution(
                        proof, delivery_missing_reason=delivery_reason
                    ),
                    "error": f"模型试图结束运行，但未检测到真实交付证据：{delivery_reason}",
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
        plan = await mark_progress(
            bridge.plan, session_id=session_id, run_id=run_id, fail_current=True
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
        return {
            "error": str(exc),
            "finished": True,
            "plan": plan,
            "execution_summary": _summarize_execution(bridge.proof),
        }

    log.info(
        "graph.execute_agent_sdk.done",
        session_id=str(session_id),
        turns=tool_turns,
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
        "tool_turns": tool_turns,
        "plan": plan,
        "execution_summary": _summarize_execution(proof),
        "finished": True,
    }
