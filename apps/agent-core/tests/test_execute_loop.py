"""Unit test for the execute node's tool-calling loop.

Streaming and HTTP are patched so the loop logic is exercised in isolation.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from app.graph.nodes import execute as exe
from app.tools.client import ToolManifest, ToolResult


class _SettingsStub:
    agent_max_turns = 40
    agent_max_total_turns = 80
    agent_default_executor = "agent-executor"
    agent_default_coder = "agent-coder"
    agent_default_skill = "agent-skill"


@pytest.mark.asyncio
async def test_repeated_stop_with_unproductive_tool_reaches_recovery():
    stream = _StreamStub([
        ("完成", [], (1, 1)),
        ("", [_pending("check", "shell", '{"cmd":"ls"}')], (1, 1)),
        ("完成", [], (1, 1)),
    ])
    mcp = AsyncMock()
    mcp.invoke.return_value = ToolResult(ok=True, output={"stdout": "", "cmd": "ls"})
    with (
        patch.object(exe, "_stream_one_turn", stream),
        patch.object(exe, "_stop_blocked_reason", AsyncMock(return_value="当前步骤尚未验证")),
        patch.object(exe, "maybe_compact", AsyncMock(side_effect=lambda messages, **_: (messages, False, None))),
        patch.object(exe, "emit", AsyncMock()),
        patch.object(exe, "get_async_openai", return_value=object()),
        patch.object(exe, "tool_manifest_cache", return_value=_shell_manifest_cache()),
        patch.object(exe, "build_system_prompt", return_value="sys"),
        patch.object(exe, "get_client", return_value=mcp),
        patch.object(exe, "get_settings", return_value=_SettingsStub()),
    ):
        state = await exe.execute_node({
            "session_id": uuid4(), "run_id": "retry-test", "sandbox_id": "test",
            "user_message": "检查任务", "messages": [], "tool_turns": 0,
        })
    assert stream.calls == 3
    assert state["tool_turns"] == 1
    assert state["execution_summary"]["delivery_missing_reason"] == "当前步骤尚未验证"


@pytest.fixture(autouse=True)
def _isolate_execute_unit_side_effects():
    """Loop tests must not require a live DB or MCP artifact store."""
    with (
        patch.object(exe, "emit_model_usage", AsyncMock()) as usage,
        patch.object(exe, "sync_plan_artifact", AsyncMock()),
        patch.object(exe, "_append_executor_progress", AsyncMock()),
    ):
        yield usage


# --- helpers ---


class _StreamStub:
    """Fake `_stream_one_turn` that returns scripted turns one at a time."""

    def __init__(self, turns: list[tuple[str, list, tuple[int, int]]]):
        self._turns = list(turns)
        self.calls = 0

    async def __call__(self, **kwargs):
        self.calls += 1
        if not self._turns:
            return "", [], (0, 0)
        return self._turns.pop(0)


def _pending(id_: str, name: str, args_json: str):
    pc = exe._PendingToolCall()
    pc.id = id_
    pc.name = name
    pc.args_buf = args_json
    return pc


def _shell_manifest_cache() -> dict[str, ToolManifest]:
    return {
        "shell": ToolManifest(
            name="shell",
            description="Run a shell command",
            input_schema={"type": "object", "properties": {"cmd": {"type": "string"}}},
            category="os",
            mutates=True,
        )
    }


# --- tests ---


def test_completed_plan_allows_repair_but_missing_active_step_does_not():
    plan = {"todos": [{"id": "1", "text": "交付文件", "status": "done"}]}
    assert exe.active_todo_allows_tool(plan, "shell", available_tool_names={"shell"})
    assert not exe.active_todo_allows_tool(plan, "absent", available_tool_names={"shell"})
    plan["todos"][0]["status"] = "failed"
    assert not exe.active_todo_allows_tool(plan, "shell", available_tool_names={"shell"})


def test_hint_does_not_block_supporting_or_alternative_tools():
    plan = {
        "todos": [{"id": "1", "text": "生成视频所需配图", "status": "in_progress", "tool_hint": "wan_i2v"}]
    }
    assert exe.active_todo_allows_tool(plan, "wan_text2image")
    plan["todos"][0].update(text="抓取网页资料", tool_hint="search")
    assert exe.active_todo_allows_tool(plan, "browser")


def test_task_recovery_requires_own_step_completion_and_survives_checkpoint():
    before = {"todos": [{"id": "1", "text": "生成报告", "status": "in_progress"}]}
    after = {"todos": [{"id": "1", "text": "生成报告", "status": "done"}]}
    failure = exe._ExecutionProof(failed_tool_calls=1)
    exe._track_task_recovery(failure, before, before)
    proof = exe._proof_from_execution_summary(exe._summarize_execution(failure))
    unrelated = exe._ExecutionProof(successful_tool_calls=1)
    exe._track_task_recovery(unrelated, before, before)
    exe._merge_proof(proof, unrelated)
    assert exe._unrecovered_failure_count(proof) == 1
    recovery = exe._ExecutionProof(successful_tool_calls=1, written_paths={"report.pdf"})
    exe._track_task_recovery(recovery, before, after)
    exe._merge_proof(proof, recovery)
    assert exe._unrecovered_failure_count(proof) == 0
    assert proof.pending_task_failures == {}
    assert proof.failed_tool_calls == 1


def test_completed_different_task_does_not_clear_failure():
    proof = exe._ExecutionProof(failed_tool_calls=1, pending_task_failures={"生成报告": 1})
    recovery = exe._ExecutionProof(successful_tool_calls=1, resolved_tasks={"生成图片"})
    exe._merge_proof(proof, recovery)
    assert exe._unrecovered_failure_count(proof) == 1


def test_auto_recovery_is_not_counted_twice():
    before = {"todos": [{"id": "1", "text": "生成报告", "status": "in_progress"}]}
    after = {"todos": [{"id": "1", "text": "生成报告", "status": "done"}]}
    delta = exe._ExecutionProof(failed_tool_calls=1, recovered_failures=1, successful_tool_calls=1)
    exe._track_task_recovery(delta, before, after)
    proof = exe._merge_proof(exe._ExecutionProof(), delta)
    assert proof.recovered_failures == 1
    assert exe._unrecovered_failure_count(proof) == 0


def test_tool_operation_key_is_stable_and_argument_sensitive():
    base = {
        "run_id": "run-1",
        "operation_index": "0:4:1",
        "tool_name": "filesystem",
    }
    first = exe._tool_operation_key(args={"path": "a", "action": "write"}, **base)
    reordered = exe._tool_operation_key(args={"action": "write", "path": "a"}, **base)
    changed = exe._tool_operation_key(args={"action": "write", "path": "b"}, **base)

    assert first == reordered
    assert first != changed
    assert first.startswith("run-1:tool:0:4:1:filesystem:")


def test_has_execution_progress_requires_tool_activity():
    empty = exe._ExecutionProof()
    assert exe._has_execution_progress(empty) is False
    assert exe._has_execution_progress(exe._ExecutionProof(successful_tool_calls=1)) is True
    assert exe._has_execution_progress(exe._ExecutionProof(failed_tool_calls=1)) is True
    assert exe._has_execution_progress(exe._ExecutionProof(scheduler_rejections=1)) is True
    assert exe._has_execution_progress(exe._ExecutionProof(written_paths={"a.html"})) is True


def test_task_frame_scope_filters_tools_and_restricts_filesystem_actions():
    manifests = [
        ToolManifest(
            name="shell", description="", input_schema={"type": "object", "properties": {}}, category="os"
        ),
        ToolManifest(
            name="filesystem",
            description="",
            input_schema={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["read", "list", "stat", "write", "append"]}
                },
            },
            category="file",
        ),
        ToolManifest(
            name="search", description="", input_schema={"type": "object", "properties": {}}, category="net"
        ),
    ]
    allowed = exe.manifests_allowed_by_task_frame(
        manifests, {"allowed_action_scope": ["file_read", "search"]}
    )
    assert [manifest.name for manifest in allowed] == ["filesystem", "search"]
    action = allowed[0].input_schema["properties"]["action"]
    assert action["enum"] == ["read", "list", "stat"]

    browser_read_fallback = exe.manifests_allowed_by_task_frame(
        manifests, {"allowed_action_scope": ["browser_read"]}
    )
    assert [manifest.name for manifest in browser_read_fallback] == ["search"]


def test_active_todo_blocks_future_step_tool():
    plan = {
        "todos": [
            {"id": "1", "text": "编写并保存旁白脚本", "status": "in_progress"},
            {"id": "2", "text": "生成旁白音频", "status": "pending"},
        ]
    }
    assert exe.active_todo_allows_tool(plan, "filesystem") is True
    assert exe.active_todo_allows_tool(plan, "minimax_tts") is False


def test_active_image_todo_allows_alternative_tools_without_future_step():
    plan = {"todos": [{"id": "1", "text": "生成 2 张配图", "status": "in_progress"}]}
    assert exe.active_todo_allows_tool(plan, "wan_text2image") is True
    assert exe.active_todo_allows_tool(plan, "wan_t2v") is True


def test_active_video_todo_allows_available_wan_video_tools():
    plan = {
        "todos": [
            {
                "id": "3",
                "text": "生成 5 段动态视频",
                "status": "in_progress",
                "tool_hint": "wan_i2v",
            }
        ]
    }
    available = {"wan_i2v", "wan_t2v", "wan_r2v", "filesystem", "shell"}
    assert exe.active_todo_allows_tool(plan, "wan_i2v", available_tool_names=available) is True
    assert exe.active_todo_allows_tool(plan, "wan_t2v", available_tool_names=available) is True


def test_active_todo_honors_explicit_tool_hint():
    plan = {
        "todos": [
            {
                "id": "1",
                "text": "完成当前素材步骤",
                "status": "in_progress",
                "tool_hint": "wan_text2image",
            }
        ]
    }
    assert exe.active_todo_allows_tool(plan, "wan_text2image") is True


def test_active_todo_maps_legacy_browser_hint_to_real_search_tool():
    plan = {
        "todos": [
            {
                "id": "1",
                "text": "获取乔布斯的维基百科生平介绍文本",
                "status": "in_progress",
                "tool_hint": "browser",
            }
        ]
    }
    assert exe.active_todo_allows_tool(plan, "search") is True
    assert exe.active_todo_allows_tool(plan, "wan_i2v") is True


def test_ambiguous_unhinted_todo_keeps_executor_flexible():
    plan = {"todos": [{"id": "1", "text": "处理当前素材", "status": "in_progress"}]}
    assert exe.active_todo_allows_tool(plan, "search") is True
    assert exe.active_todo_allows_tool(plan, "visual_critique") is True

    stale = {
        "todos": [
            {
                "id": "1",
                "text": "处理当前素材",
                "status": "in_progress",
                "tool_hint": "removed_legacy_tool",
            }
        ]
    }
    assert (
        exe.active_todo_allows_tool(
            stale,
            "visual_critique",
            available_tool_names={"visual_critique"},
        )
        is True
    )

    future_media = {
        "todos": [
            {"id": "1", "text": "处理当前素材", "status": "in_progress"},
            {
                "id": "2",
                "text": "生成动态视频",
                "status": "pending",
                "tool_hint": "wan_i2v",
            },
        ]
    }
    assert exe.active_todo_allows_tool(future_media, "wan_i2v") is False


def test_replace_active_todo_instruction_removes_stale_step():
    messages: list[Any] = []
    first = {"todos": [{"id": "1", "text": "第一步", "status": "in_progress"}]}
    second = {"todos": [{"id": "2", "text": "第二步", "status": "in_progress"}]}
    exe._replace_active_todo_instruction(messages, first)
    exe._replace_active_todo_instruction(messages, second)
    scheduler_messages = [
        message
        for message in messages
        if isinstance(message, SystemMessage) and str(message.content).startswith(exe._ACTIVE_TODO_HEADER)
    ]
    assert len(scheduler_messages) == 1
    assert "第二步" in str(scheduler_messages[0].content)
    assert "第一步" not in str(scheduler_messages[0].content)


def test_executor_extra_context_reinjects_plan_and_compact_memory():
    context = exe._compose_executor_extra_context(
        "长期记忆",
        {"task_mode": "build", "should_invoke_planner": True},
        plan={
            "todos": [
                {"id": "1", "text": "生成报告", "status": "in_progress"},
                {"id": "2", "text": "验证结果", "status": "pending"},
            ]
        },
        compact_memory="此前已经收集数据集。",
    )

    assert context is not None
    assert "[in_progress] 生成报告" in context
    assert "[pending] 验证结果" in context
    assert "此前已经收集数据集" in context
    assert "ask_user" in context
    assert "不得要求用户‘授权放行’内部调度器" in context


def test_high_autonomy_confirmation_policy_keeps_internal_recovery_autonomous():
    context = exe._compose_executor_extra_context(
        None,
        {"autonomy_level": "high", "risk_level": "low"},
    )
    assert context is not None
    assert "高自主模式" in context
    assert "内部调度冲突" in context
    assert "直接执行到底" in context


def test_proof_rehydration_avoids_false_delivery_missing_after_reflect():
    """Second `execute` pass must not reset proof; see `reflect` → `execute` loop."""
    summary = {
        "successful_tool_calls": 1,
        "mutating_tool_calls": 0,
        "non_search_tool_calls": 1,
        "mock_search_calls": 0,
        "written_paths": ["/workspace/jobs-words"],
        "verified_paths": [],
    }
    proof = exe._proof_from_execution_summary(summary)
    reason = exe._missing_delivery_reason(
        user_message="制作乔布斯词云，输出 png 文件",
        plan={
            "todos": [
                {"id": "1", "text": "下载文本", "status": "done"},
                {"id": "2", "text": "清洗合并", "status": "in_progress"},
                {"id": "3", "text": "生成词云图", "status": "pending"},
            ]
        },
        proof=proof,
    )
    assert reason is None

    empty = exe._ExecutionProof()
    reason_empty = exe._missing_delivery_reason(
        user_message="制作乔布斯词云，输出 png 文件",
        plan={
            "todos": [
                {"id": "1", "text": "下载文本", "status": "done"},
                {"id": "2", "text": "清洗合并", "status": "in_progress"},
            ]
        },
        proof=empty,
    )
    assert reason_empty is not None
    assert "没有任何成功的工具执行" in reason_empty


@pytest.mark.asyncio
async def test_text_only_turn_finishes_without_tool_calls(_isolate_execute_unit_side_effects):
    sid = uuid4()
    stream = _StreamStub([("hello world", [], (10, 5))])

    emitted: list[Any] = []

    async def _emit(ev):
        emitted.append(ev)

    with (
        patch.object(exe, "_stream_one_turn", stream),
        patch.object(
            exe, "maybe_compact", AsyncMock(side_effect=lambda messages, **_: (messages, False, None))
        ),
        patch.object(exe, "emit", _emit),
        patch.object(exe, "get_async_openai", return_value=object()),
        patch.object(exe, "tool_manifest_cache", return_value={}),
        patch.object(exe, "build_system_prompt", return_value="sys"),
        patch.object(exe, "get_settings", return_value=_SettingsStub()),
    ):
        state = await exe.execute_node(
            {
                "session_id": sid,
                "run_id": "r1",
                "executor_model": "agent-executor",
                "sandbox_id": str(sid),
                "messages": [],
                "tool_turns": 0,
            }
        )

    assert state["finished"] is True
    assert state["assistant_text"] == "hello world"
    assert state["tool_turns"] == 0
    assert state["total_agent_turns"] == 1
    assert state["total_execution_tokens"] == 15
    assert stream.calls == 1
    _isolate_execute_unit_side_effects.assert_awaited_once()


@pytest.mark.asyncio
async def test_execute_exception_preserves_checkpoint_messages_and_turn_state():
    sid = uuid4()
    tool_message = ToolMessage(content="已执行", tool_call_id="call_1")
    messages = [
        HumanMessage(content="当前任务"),
        AIMessage(
            content="",
            tool_calls=[{"name": "shell", "args": {"cmd": "pwd"}, "id": "call_1"}],
        ),
        tool_message,
    ]

    with (
        patch.object(exe, "_stream_one_turn", AsyncMock(side_effect=RuntimeError("boom"))),
        patch.object(
            exe,
            "maybe_compact",
            AsyncMock(return_value=(messages, False, None)),
        ),
        patch.object(exe, "emit", AsyncMock()),
        patch.object(exe, "get_async_openai", return_value=object()),
        patch.object(exe, "tool_manifest_cache", return_value={}),
        patch.object(exe, "build_system_prompt", return_value="sys"),
        patch.object(exe, "get_settings", return_value=_SettingsStub()),
    ):
        state = await exe.execute_node(
            {
                "session_id": sid,
                "run_id": "r1",
                "executor_model": "agent-executor",
                "sandbox_id": str(sid),
                "messages": messages,
                "tool_turns": 3,
                "compact_memory": "已有摘要",
            }
        )

    assert state["error"] == "boom"
    assert state["tool_turns"] == 3
    assert state["compact_memory"] == "已有摘要"
    assert tool_message in state["messages"]


@pytest.mark.asyncio
async def test_scheduler_rejection_returns_to_reflect_without_another_model_turn():
    sid = uuid4()
    stream = _StreamStub([("调整计划", [_pending("c1", "shell", '{"cmd":"echo hi"}')], (8, 4))])
    plan = {"todos": [{"id": "1", "text": "当前步骤", "status": "in_progress"}]}
    with (
        patch.object(exe, "_stream_one_turn", stream),
        patch.object(
            exe, "maybe_compact", AsyncMock(side_effect=lambda messages, **_: (messages, False, None))
        ),
        patch.object(exe, "emit", AsyncMock()),
        patch.object(exe, "get_async_openai", return_value=object()),
        patch.object(exe, "tool_manifest_cache", return_value=_shell_manifest_cache()),
        patch.object(exe, "build_system_prompt", return_value="sys"),
        patch.object(exe, "get_settings", return_value=_SettingsStub()),
        patch.object(
            exe,
            "_run_tool_calls",
            AsyncMock(return_value=(exe._ExecutionProof(scheduler_rejections=1), plan)),
        ),
    ):
        state = await exe.execute_node({"session_id": sid, "run_id": "r1", "messages": [], "plan": plan})
    assert stream.calls == 1
    assert state["execution_summary"]["scheduler_rejections"] == 1
    assert state["tool_turns"] == 1
    assert "error" not in state


@pytest.mark.asyncio
async def test_execute_exception_with_proof_does_not_set_terminal_error():
    sid = uuid4()

    class _BoomAfterTools(_StreamStub):
        async def __call__(self, **kwargs):
            if self.calls == 0:
                self.calls += 1
                return self._turns.pop(0)
            self.calls += 1
            raise RuntimeError("hub down")

    stream = _BoomAfterTools([("", [_pending("cid_1", "shell", '{"cmd":"echo hi"}')], (8, 4))])
    mcp = AsyncMock()
    mcp.invoke = AsyncMock(
        return_value=ToolResult(ok=True, preview="hi\n", output={"exit_code": 0, "cmd": "echo hi"})
    )

    with (
        patch.object(exe, "_stream_one_turn", stream),
        patch.object(
            exe, "maybe_compact", AsyncMock(side_effect=lambda messages, **_: (messages, False, None))
        ),
        patch.object(exe, "emit", AsyncMock()),
        patch.object(exe, "emit_model_usage", AsyncMock()),
        patch.object(exe, "get_async_openai", return_value=object()),
        patch.object(exe, "tool_manifest_cache", return_value=_shell_manifest_cache()),
        patch.object(exe, "build_system_prompt", return_value="sys"),
        patch.object(exe, "get_client", return_value=mcp),
        patch.object(exe, "get_settings", return_value=_SettingsStub()),
    ):
        state = await exe.execute_node(
            {
                "session_id": sid,
                "run_id": "r1",
                "executor_model": "agent-executor",
                "sandbox_id": str(sid),
                "user_message": "说一声 hi",
                "messages": [],
                "tool_turns": 0,
            }
        )

    assert "error" not in state
    assert state["finished"] is True
    assert "hub down" in (state["execution_summary"].get("execute_exception") or "")
    assert int(state["execution_summary"].get("successful_tool_calls") or 0) >= 1


@pytest.mark.asyncio
async def test_execute_injects_retrieved_memories_into_system_prompt():
    sid = uuid4()
    stream = _StreamStub([("done", [], (2, 1))])
    captured: dict[str, Any] = {}

    def _build_prompt(**kwargs):
        captured["extra_context"] = kwargs.get("extra_context")
        return "sys"

    with (
        patch.object(exe, "_stream_one_turn", stream),
        patch.object(
            exe, "maybe_compact", AsyncMock(side_effect=lambda messages, **_: (messages, False, None))
        ),
        patch.object(exe, "emit", AsyncMock()),
        patch.object(exe, "get_async_openai", return_value=object()),
        patch.object(exe, "tool_manifest_cache", return_value={}),
        patch.object(exe, "build_system_prompt", side_effect=_build_prompt),
        patch.object(
            exe,
            "search_memories",
            AsyncMock(return_value=[SimpleNamespace(id="m1", text="用户偏好：输出尽量简洁")]),
        ),
        patch.object(exe, "get_settings", return_value=_SettingsStub()),
    ):
        state = await exe.execute_node(
            {
                "session_id": sid,
                "run_id": "r1",
                "executor_model": "agent-executor",
                "sandbox_id": str(sid),
                "user_message": "继续刚才的话题",
                "messages": [],
                "tool_turns": 0,
            }
        )

    assert state["assistant_text"] == "done"
    assert "长期记忆" in str(captured["extra_context"])
    assert "输出尽量简洁" in str(captured["extra_context"])


@pytest.mark.asyncio
async def test_tool_call_turn_invokes_mcp_and_loops_again():
    sid = uuid4()
    first_turn = (
        "",
        [_pending("cid_1", "shell", '{"cmd":"echo hi"}')],
        (8, 4),
    )
    second_turn = ("done", [], (3, 2))
    stream = _StreamStub([first_turn, second_turn])

    emitted: list[Any] = []

    async def _emit(ev):
        emitted.append(ev)

    mcp = AsyncMock()
    mcp.invoke = AsyncMock(
        return_value=ToolResult(ok=True, preview="hi\n", output={"exit_code": 0}, duration_ms=3)
    )

    with (
        patch.object(exe, "_stream_one_turn", stream),
        patch.object(
            exe, "maybe_compact", AsyncMock(side_effect=lambda messages, **_: (messages, False, None))
        ),
        patch.object(exe, "emit", _emit),
        patch.object(exe, "get_async_openai", return_value=object()),
        patch.object(exe, "tool_manifest_cache", return_value=_shell_manifest_cache()),
        patch.object(exe, "build_system_prompt", return_value="sys"),
        patch.object(exe, "get_client", return_value=mcp),
        patch.object(exe, "get_settings", return_value=_SettingsStub()),
    ):
        state = await exe.execute_node(
            {
                "session_id": sid,
                "run_id": "r1",
                "executor_model": "agent-executor",
                "sandbox_id": str(sid),
                "messages": [],
                "tool_turns": 0,
            }
        )

    assert state["assistant_text"] == "done"
    assert state["tool_turns"] == 1
    assert stream.calls == 2
    mcp.invoke.assert_awaited_once()
    assert mcp.invoke.await_args.kwargs["idempotency_key"] == exe._tool_operation_key(
        run_id="r1",
        operation_index="0:1:0",
        tool_name="shell",
        args={"cmd": "echo hi"},
    )
    # tool.call + tool.result events fired
    types = [type(e).__name__ for e in emitted]
    assert "ToolCallEvent" in types
    assert "ToolResultEvent" in types


@pytest.mark.asyncio
async def test_max_turns_short_circuits():
    sid = uuid4()

    async def _stream(**_):
        return ("", [_pending("cid", "shell", "{}")], (1, 1))

    emitted: list[Any] = []

    async def _emit(ev):
        emitted.append(ev)

    mcp = AsyncMock()
    mcp.invoke = AsyncMock(return_value=ToolResult(ok=True, preview="", output={}))

    # Lightweight settings stub with just the fields execute_node touches.
    class _S:
        agent_max_turns = 2
        agent_default_executor = "agent-executor"
        agent_default_coder = "agent-coder"
        agent_default_skill = "agent-skill"

    with (
        patch.object(exe, "_stream_one_turn", _stream),
        patch.object(
            exe, "maybe_compact", AsyncMock(side_effect=lambda messages, **_: (messages, False, None))
        ),
        patch.object(exe, "emit", _emit),
        patch.object(exe, "get_async_openai", return_value=object()),
        patch.object(exe, "tool_manifest_cache", return_value=_shell_manifest_cache()),
        patch.object(exe, "build_system_prompt", return_value="sys"),
        patch.object(exe, "get_client", return_value=mcp),
        patch.object(exe, "get_settings", return_value=_S()),
    ):
        state = await exe.execute_node(
            {
                "session_id": sid,
                "run_id": "r1",
                "executor_model": "agent-executor",
                "sandbox_id": str(sid),
                "messages": [],
                "tool_turns": 0,
            }
        )

    assert state["finished"] is True
    assert "工具执行轮数上限（2/2）" in state["assistant_text"]
    assert state["tool_turns"] == 2


@pytest.mark.asyncio
async def test_execute_persists_compact_memory_when_summary_generated():
    sid = uuid4()
    stream = _StreamStub([("done", [], (2, 1))])

    async def _compact(messages, **_):
        return messages, True, "历史摘要"

    with (
        patch.object(exe, "_stream_one_turn", stream),
        patch.object(exe, "maybe_compact", AsyncMock(side_effect=_compact)),
        patch.object(exe, "emit", AsyncMock()),
        patch.object(exe, "get_async_openai", return_value=object()),
        patch.object(exe, "tool_manifest_cache", return_value={}),
        patch.object(exe, "build_system_prompt", return_value="sys"),
        patch.object(exe, "get_settings", return_value=_SettingsStub()),
    ):
        state = await exe.execute_node(
            {
                "session_id": sid,
                "run_id": "r1",
                "executor_model": "agent-executor",
                "sandbox_id": str(sid),
                "messages": [],
                "tool_turns": 0,
                "compact_memory": None,
            }
        )

    assert state["compact_memory"] == "历史摘要"


@pytest.mark.asyncio
async def test_execute_rejects_fake_done_for_artifact_goal_without_tool_proof():
    sid = uuid4()
    stream = _StreamStub(
        [
            ("DONE: 已完成房价分析网站", [], (6, 3)),
            ("DONE: 网站已经准备好", [], (4, 2)),
        ]
    )

    with (
        patch.object(exe, "_stream_one_turn", stream),
        patch.object(
            exe, "maybe_compact", AsyncMock(side_effect=lambda messages, **_: (messages, False, None))
        ),
        patch.object(exe, "emit", AsyncMock()),
        patch.object(exe, "get_async_openai", return_value=object()),
        patch.object(exe, "tool_manifest_cache", return_value={}),
        patch.object(exe, "build_system_prompt", return_value="sys"),
        patch.object(exe, "get_settings", return_value=_SettingsStub()),
    ):
        state = await exe.execute_node(
            {
                "session_id": sid,
                "run_id": "r1",
                "executor_model": "agent-executor",
                "sandbox_id": str(sid),
                "user_message": "请帮我做一个房价分析网站",
                "messages": [],
                "tool_turns": 0,
                "plan": {
                    "todos": [
                        {"id": "1", "text": "抓数据", "status": "pending"},
                        {"id": "2", "text": "写前端", "status": "pending"},
                    ]
                },
            }
        )

    assert state["finished"] is True
    assert "error" not in state
    reason = state["execution_summary"]["delivery_missing_reason"] or ""
    assert "未检测到真实交付证据" in reason or "未完成" in reason


@pytest.mark.asyncio
async def test_execute_forces_shell_tool_after_missing_delivery_proof():
    sid = uuid4()
    stream = AsyncMock(
        side_effect=[
            ("DONE: 已完成房价分析网站", [], (6, 3)),
            ("DONE: 网站已经准备好", [], (4, 2)),
        ]
    )

    manifests = {
        "shell": ToolManifest(
            name="shell", description="", input_schema={"type": "object", "properties": {}}, mutates=True
        ),
        "filesystem": ToolManifest(
            name="filesystem", description="", input_schema={"type": "object", "properties": {}}, mutates=True
        ),
    }

    with (
        patch.object(exe, "_stream_one_turn", stream),
        patch.object(
            exe, "maybe_compact", AsyncMock(side_effect=lambda messages, **_: (messages, False, None))
        ),
        patch.object(exe, "emit", AsyncMock()),
        patch.object(exe, "get_async_openai", return_value=object()),
        patch.object(exe, "tool_manifest_cache", return_value=manifests),
        patch.object(exe, "build_system_prompt", return_value="sys"),
        patch.object(exe, "get_settings", return_value=_SettingsStub()),
    ):
        state = await exe.execute_node(
            {
                "session_id": sid,
                "run_id": "r1",
                "executor_model": "agent-executor",
                "sandbox_id": str(sid),
                "user_message": "请帮我做一个房价分析网站",
                "messages": [],
                "tool_turns": 0,
                "plan": {
                    "todos": [
                        {"id": "1", "text": "抓数据", "status": "pending"},
                        {"id": "2", "text": "写前端", "status": "pending"},
                    ]
                },
            }
        )

    assert state["finished"] is True
    assert stream.await_count == 2
    assert stream.await_args_list[0].kwargs["forced_tool_name"] is None
    assert stream.await_args_list[1].kwargs["forced_tool_name"] == "shell"
    assert "error" not in state
    reason = state["execution_summary"]["delivery_missing_reason"] or ""
    assert "未检测到真实交付证据" in reason or "未完成" in reason


@pytest.mark.asyncio
async def test_execute_auto_recovers_missing_python_dependency():
    sid = uuid4()
    stream = _StreamStub(
        [
            ("", [_pending("cid_1", "shell", '{"cmd":"python app.py"}')], (8, 4)),
            ("done", [], (3, 2)),
        ]
    )

    emitted: list[Any] = []

    async def _emit(ev):
        emitted.append(ev)

    mcp = AsyncMock()
    mcp.invoke = AsyncMock(
        side_effect=[
            ToolResult(
                ok=False,
                error="exit code 1",
                preview="ModuleNotFoundError: No module named 'pandas'",
                output={
                    "exit_code": 1,
                    "stdout": "Traceback...\nModuleNotFoundError: No module named 'pandas'\n",
                    "cmd": "python app.py",
                    "cwd": "/tmp/sandbox",
                },
            ),
            ToolResult(
                ok=True,
                preview="installed pandas",
                output={"exit_code": 0, "stdout": "installed pandas", "cmd": "python -m pip install pandas"},
            ),
            ToolResult(
                ok=True,
                preview="script ok",
                output={"exit_code": 0, "stdout": "ok", "cmd": "python app.py"},
            ),
        ]
    )

    manifests = {
        "shell": ToolManifest(
            name="shell", description="", input_schema={"type": "object", "properties": {}}, mutates=True
        ),
    }

    with (
        patch.object(exe, "_stream_one_turn", stream),
        patch.object(
            exe, "maybe_compact", AsyncMock(side_effect=lambda messages, **_: (messages, False, None))
        ),
        patch.object(exe, "emit", _emit),
        patch.object(exe, "get_async_openai", return_value=object()),
        patch.object(exe, "tool_manifest_cache", return_value=manifests),
        patch.object(exe, "build_system_prompt", return_value="sys"),
        patch.object(exe, "get_client", return_value=mcp),
        patch.object(exe, "get_settings", return_value=_SettingsStub()),
    ):
        state = await exe.execute_node(
            {
                "session_id": sid,
                "run_id": "r1",
                "executor_model": "agent-executor",
                "sandbox_id": str(sid),
                "user_message": "运行这个 python 脚本",
                "messages": [],
                "tool_turns": 0,
            }
        )

    assert state["assistant_text"] == "done"
    assert mcp.invoke.await_count == 3
    install_args = mcp.invoke.await_args_list[1].kwargs["args"]
    assert install_args["cmd"] == "python -m pip install pandas"
    event_names = [getattr(e, "name", None) for e in emitted if type(e).__name__ == "ToolCallEvent"]
    assert event_names == ["shell", "shell", "shell"]


@pytest.mark.asyncio
async def test_execute_auto_recovers_missing_node_dependency():
    sid = uuid4()
    stream = _StreamStub(
        [
            ("", [_pending("cid_1", "shell", '{"cmd":"node app.js"}')], (8, 4)),
            ("done", [], (3, 2)),
        ]
    )

    mcp = AsyncMock()
    mcp.invoke = AsyncMock(
        side_effect=[
            ToolResult(
                ok=False,
                error="exit code 1",
                preview="Error: Cannot find module 'axios'",
                output={
                    "exit_code": 1,
                    "stdout": "Error: Cannot find module 'axios'\n",
                    "cmd": "node app.js",
                    "cwd": "/tmp/sandbox",
                },
            ),
            ToolResult(
                ok=True,
                preview="installed axios",
                output={"exit_code": 0, "stdout": "installed axios", "cmd": "npm install axios"},
            ),
            ToolResult(
                ok=True,
                preview="script ok",
                output={"exit_code": 0, "stdout": "ok", "cmd": "node app.js"},
            ),
        ]
    )

    with (
        patch.object(exe, "_stream_one_turn", stream),
        patch.object(
            exe, "maybe_compact", AsyncMock(side_effect=lambda messages, **_: (messages, False, None))
        ),
        patch.object(exe, "emit", AsyncMock()),
        patch.object(exe, "get_async_openai", return_value=object()),
        patch.object(exe, "tool_manifest_cache", return_value=_shell_manifest_cache()),
        patch.object(exe, "build_system_prompt", return_value="sys"),
        patch.object(exe, "get_client", return_value=mcp),
        patch.object(exe, "get_settings", return_value=_SettingsStub()),
    ):
        state = await exe.execute_node(
            {
                "session_id": sid,
                "run_id": "r1",
                "executor_model": "agent-executor",
                "sandbox_id": str(sid),
                "user_message": "运行这个 node 脚本",
                "messages": [],
                "tool_turns": 0,
            }
        )

    assert state["assistant_text"] == "done"
    assert mcp.invoke.await_count == 3
    install_args = mcp.invoke.await_args_list[1].kwargs["args"]
    assert install_args["cmd"] == "npm install axios"


@pytest.mark.asyncio
async def test_execute_auto_recovers_missing_frontend_binary():
    sid = uuid4()
    stream = _StreamStub(
        [
            ("", [_pending("cid_1", "shell", '{"cmd":"next dev","cwd":"/tmp/app"}')], (8, 4)),
            ("done", [], (3, 2)),
        ]
    )

    mcp = AsyncMock()
    mcp.invoke = AsyncMock(
        side_effect=[
            ToolResult(
                ok=False,
                error="exit code 127",
                preview="sh: 1: next: not found",
                output={
                    "exit_code": 127,
                    "stdout": "sh: 1: next: not found\n",
                    "cmd": "next dev",
                    "cwd": "/tmp/app",
                },
            ),
            ToolResult(
                ok=True,
                preview="installed dependencies",
                output={
                    "exit_code": 0,
                    "stdout": "installed",
                    "cmd": "if [ -f package.json ]; then npm install; else exit 1; fi",
                },
            ),
            ToolResult(
                ok=True,
                preview="server started",
                output={"exit_code": 0, "stdout": "ready", "cmd": "next dev"},
            ),
        ]
    )

    with (
        patch.object(exe, "_stream_one_turn", stream),
        patch.object(
            exe, "maybe_compact", AsyncMock(side_effect=lambda messages, **_: (messages, False, None))
        ),
        patch.object(exe, "emit", AsyncMock()),
        patch.object(exe, "get_async_openai", return_value=object()),
        patch.object(exe, "tool_manifest_cache", return_value=_shell_manifest_cache()),
        patch.object(exe, "build_system_prompt", return_value="sys"),
        patch.object(exe, "get_client", return_value=mcp),
        patch.object(exe, "get_settings", return_value=_SettingsStub()),
    ):
        state = await exe.execute_node(
            {
                "session_id": sid,
                "run_id": "r1",
                "executor_model": "agent-executor",
                "sandbox_id": str(sid),
                "user_message": "启动 next 项目",
                "messages": [],
                "tool_turns": 0,
            }
        )

    assert state["assistant_text"] == "done"
    assert mcp.invoke.await_count == 3
    install_args = mcp.invoke.await_args_list[1].kwargs["args"]
    assert install_args["cmd"] == "if [ -f package.json ]; then npm install; else exit 1; fi"
    assert install_args["cwd"] == "/tmp/app"


def test_goal_requires_real_artifact_ignores_run_script_requests():
    assert (
        exe._goal_requires_real_artifact(
            "运行这个 python 脚本并把报错修掉",
            None,
        )
        is False
    )


def test_goal_requires_real_artifact_from_task_frame_deliverable_type():
    assert (
        exe._goal_requires_real_artifact(
            "解释一下 REST 是什么",
            None,
            {"deliverable_type": "web_app"},
        )
        is True
    )
    assert (
        exe._goal_requires_real_artifact(
            "解释一下 REST 是什么",
            None,
            {"deliverable_type": "direct_answer"},
        )
        is False
    )


def test_goal_requires_real_artifact_for_create_script_requests():
    assert (
        exe._goal_requires_real_artifact(
            "帮我写一个数据清洗脚本",
            None,
        )
        is True
    )


def test_normalize_node_package_name_keeps_scoped_package():
    assert exe._normalize_node_package_name("@types/node/register") == "@types/node"
    assert exe._normalize_node_package_name("axios/lib/core/Axios") == "axios"


def test_goal_requires_real_artifact_ignores_start_project_requests():
    assert (
        exe._goal_requires_real_artifact(
            "帮我启动这个 next 项目",
            None,
        )
        is False
    )


def test_proof_from_filesystem_write_records_written_path():
    proof = exe._proof_from_tool_result(
        tool_name="filesystem",
        args={"action": "write", "path": "src/app.ts"},
        result=ToolResult(ok=True, preview="", output={"path": "/tmp/sandbox/src/app.ts"}),
        manifest=SimpleNamespace(mutates=True),
    )
    assert "/tmp/sandbox/src/app.ts" in proof.written_paths
    assert proof.verified_paths == set()
    assert proof.tool_names == ["filesystem"]


def test_proof_from_media_tools_record_written_paths():
    img = exe._proof_from_tool_result(
        tool_name="wan_text2image",
        args={"prompt": "x"},
        result=ToolResult(
            ok=True,
            preview="",
            output={"paths": ["artifacts/wan_t2i_test_ab12cd34_0.png"], "task_id": "t1"},
        ),
        manifest=SimpleNamespace(mutates=True),
    )
    assert "artifacts/wan_t2i_test_ab12cd34_0.png" in img.written_paths

    vid = exe._proof_from_tool_result(
        tool_name="wan_t2v",
        args={"prompt": "x"},
        result=ToolResult(
            ok=True,
            preview="video → artifacts/wan_t2v_x.mp4",
            output={"path": "artifacts/wan_t2v_x.mp4", "task_id": "t2"},
        ),
        manifest=SimpleNamespace(mutates=True),
    )
    assert "artifacts/wan_t2v_x.mp4" in vid.written_paths

    tts = exe._proof_from_tool_result(
        tool_name="minimax_tts",
        args={"text": "hi"},
        result=ToolResult(
            ok=True,
            preview="",
            output={"path": "artifacts/minimax_tts_hi_ab12cd34.mp3"},
        ),
        manifest=SimpleNamespace(mutates=True),
    )
    assert "artifacts/minimax_tts_hi_ab12cd34.mp3" in tts.written_paths


def test_proof_from_shell_command_tracks_written_and_verified_paths():
    write_proof = exe._proof_from_tool_result(
        tool_name="shell",
        args={"cmd": "mkdir src && touch src/index.ts", "cwd": "/tmp/app"},
        result=ToolResult(
            ok=True,
            preview="",
            output={"cmd": "mkdir src && touch src/index.ts", "cwd": "/tmp/app", "stdout": ""},
        ),
        manifest=SimpleNamespace(mutates=True),
    )
    verify_proof = exe._proof_from_tool_result(
        tool_name="shell",
        args={"cmd": "ls src && cat src/index.ts", "cwd": "/tmp/app"},
        result=ToolResult(
            ok=True,
            preview="",
            output={"cmd": "ls src && cat src/index.ts", "cwd": "/tmp/app", "stdout": ""},
        ),
        manifest=SimpleNamespace(mutates=True),
    )
    assert "/tmp/app/src" in write_proof.written_paths or "/tmp/app/src/index.ts" in write_proof.written_paths
    assert "/tmp/app/src/index.ts" in verify_proof.verified_paths


def test_missing_delivery_reason_rejects_mutation_without_artifact_paths():
    proof = exe._ExecutionProof(
        successful_tool_calls=1,
        mutating_tool_calls=1,
        written_paths=set(),
        verified_paths=set(),
    )
    reason = exe._missing_delivery_reason(
        user_message="帮我做一个网站",
        plan=None,
        proof=proof,
    )
    assert reason is not None
    assert "真实产物" in reason


def test_missing_delivery_reason_rejects_verified_but_unwritten_artifact():
    proof = exe._ExecutionProof(
        successful_tool_calls=1,
        non_search_tool_calls=1,
        written_paths=set(),
        verified_paths={"report.html"},
    )
    reason = exe._missing_delivery_reason(
        user_message="帮我生成一个 HTML 报告",
        plan=None,
        proof=proof,
    )
    assert reason is not None
    assert "没有检测到写文件" in reason


def test_filesystem_stat_is_verification_not_write_proof():
    proof = exe._proof_from_tool_result(
        tool_name="filesystem",
        args={"action": "stat", "path": "report.html"},
        result=ToolResult(
            ok=True,
            preview="",
            output={"path": "/tmp/sandbox/report.html", "is_file": True},
        ),
        manifest=SimpleNamespace(mutates=False),
    )
    assert "report.html" in proof.verified_paths
    assert proof.written_paths == set()


def test_missing_delivery_reason_task_frame_implies_artifact_without_keywords():
    proof = exe._ExecutionProof(successful_tool_calls=0)
    reason = exe._missing_delivery_reason(
        user_message="按惯例处理",
        plan=None,
        proof=proof,
        task_frame={"deliverable_type": "spreadsheet"},
    )
    assert reason is not None
    assert "工具" in reason


def test_missing_delivery_reason_rejects_generator_clips_when_compose_required():
    proof = exe._ExecutionProof(
        successful_tool_calls=3,
        written_paths={
            "artifacts/wan_t2v_closing_shot.mp4",
            "artifacts/wan_t2v_office.mp4",
        },
    )
    reason = exe._missing_delivery_reason(
        user_message="做一条乔布斯与盖茨的纪录片",
        plan={
            "todos": [
                {"id": "5", "text": "将视频片段合成为约 20 秒连续视频", "status": "done"},
                {"id": "6", "text": "验证最终视频", "status": "done"},
            ]
        },
        proof=proof,
        task_frame={
            "deliverable_type": "video",
            "success_criteria": ["生成横屏 16:9、约 20 秒视频", "画面与旁白同步", "交付最终视频文件路径"],
        },
    )
    assert reason is not None
    assert "成片" in reason or "素材" in reason


def test_missing_delivery_reason_accepts_non_clip_video_as_composed_delivery():
    proof = exe._ExecutionProof(
        successful_tool_calls=4,
        written_paths={
            "artifacts/wan_t2v_clip.mp4",
            "artifacts/final_documentary.mp4",
        },
    )
    reason = exe._missing_delivery_reason(
        user_message="做一条纪录片",
        plan=None,
        proof=proof,
        task_frame={
            "deliverable_type": "video",
            "success_criteria": ["交付最终视频文件"],
        },
    )
    assert reason is None


def test_unfinished_plan_blocks_stop():
    reason = exe._unfinished_plan_reason(
        {
            "todos": [
                {"id": "1", "text": "合成成片", "status": "in_progress"},
                {"id": "2", "text": "验收", "status": "pending"},
            ]
        }
    )
    assert reason is not None
    assert "未完成" in reason


def test_missing_delivery_reason_rejects_wrong_type_for_website():
    proof = exe._ExecutionProof(
        successful_tool_calls=1,
        written_paths={"notes.txt"},
    )
    reason = exe._missing_delivery_reason(
        user_message="做一个站点",
        plan=None,
        proof=proof,
        task_frame={"deliverable_type": "website"},
    )
    assert reason is not None
    assert "网页" in reason


@pytest.mark.asyncio
async def test_emit_artifact_events_emits_artifact_and_screenshot():
    proof = exe._ExecutionProof(
        written_paths={"/tmp/app/report.html", "/tmp/app/shot.png"},
    )
    with patch.object(exe, "emit", AsyncMock()) as mocked_emit:
        await exe._emit_artifact_events(
            session_id=uuid4(),
            run_id="run_1",
            proof=proof,
        )

    event_types = [call.args[0].type for call in mocked_emit.await_args_list]
    assert "artifact" in event_types
    assert "screenshot" in event_types


@pytest.mark.asyncio
async def test_invoke_tool_visual_critique_injects_default_model():
    mcp = AsyncMock()
    mcp.invoke = AsyncMock(return_value=ToolResult(ok=True, preview="ok"))
    manifest = ToolManifest(
        name="visual_critique",
        description="",
        input_schema={"type": "object", "properties": {}},
        category="media",
    )
    with patch.object(exe, "emit", AsyncMock()):
        await exe._invoke_tool_with_events(
            mcp=mcp,
            tool_name="visual_critique",
            args={"path": "x.png"},
            event_id="e1",
            sandbox_id="sb",
            session_id=uuid4(),
            run_id="r1",
            working_messages=[],
            manifest=manifest,
            mcp_tool_models={"visual_critique": "qwen3-vl-flash"},
        )
    assert mcp.invoke.await_count == 1
    kw = mcp.invoke.await_args.kwargs
    assert kw["args"]["model"] == "qwen3-vl-flash"


@pytest.mark.asyncio
async def test_invoke_tool_visual_critique_respects_explicit_model():
    mcp = AsyncMock()
    mcp.invoke = AsyncMock(return_value=ToolResult(ok=True, preview="ok"))
    manifest = ToolManifest(
        name="visual_critique",
        description="",
        input_schema={"type": "object", "properties": {}},
        category="media",
    )
    with patch.object(exe, "emit", AsyncMock()):
        await exe._invoke_tool_with_events(
            mcp=mcp,
            tool_name="visual_critique",
            args={"path": "x.png", "model": "custom-vl"},
            event_id="e1",
            sandbox_id="sb",
            session_id=uuid4(),
            run_id="r1",
            working_messages=[],
            manifest=manifest,
            mcp_tool_models={"visual_critique": "qwen3-vl-flash"},
        )
    assert mcp.invoke.await_args.kwargs["args"]["model"] == "custom-vl"


def test_thin_written_file_reason_rejects_empty_or_tiny_deliverables():
    assert exe._thin_written_file_reason(path="out.png", exists=False, is_file=False, size=0)
    assert "过小或为空" in (
        exe._thin_written_file_reason(path="out.png", exists=True, is_file=True, size=0) or ""
    )
    assert "过小或为空" in (
        exe._thin_written_file_reason(path="index.html", exists=True, is_file=True, size=8) or ""
    )
    assert exe._thin_written_file_reason(path="index.html", exists=True, is_file=True, size=128) is None
    assert exe._thin_written_file_reason(path="src", exists=True, is_file=False, size=0) is None


def test_unrecovered_failure_reason_requires_outstanding_failures():
    ok = exe._ExecutionProof(failed_tool_calls=1, recovered_failures=1)
    assert exe._unrecovered_failure_reason(ok) is None
    bad = exe._ExecutionProof(
        failed_tool_calls=2,
        recovered_failures=1,
        failure_notes=["exit_code=1"],
    )
    reason = exe._unrecovered_failure_reason(bad)
    assert reason is not None
    assert "尚未恢复" in reason
    assert "exit_code=1" in reason


def test_is_retryable_shell_failure_timeout_and_exit_code():
    timeout = ToolResult(ok=False, error="timeout", output={"timed_out": True, "cmd": "sleep 9"})
    assert exe._is_retryable_shell_failure(tool_name="shell", result=timeout) is True
    nonzero = ToolResult(ok=False, error="failed", output={"exit_code": 2, "cmd": "false"})
    assert exe._is_retryable_shell_failure(tool_name="shell", result=nonzero) is True
    ok = ToolResult(ok=True, output={"exit_code": 0})
    assert exe._is_retryable_shell_failure(tool_name="shell", result=ok) is False
    search = ToolResult(ok=False, error="timeout", output={"timed_out": True})
    assert exe._is_retryable_shell_failure(tool_name="search", result=search) is False
    billing = ToolResult(ok=False, error="积分不足，调用 shell 还需冻结 1 积分")
    assert exe._is_retryable_shell_failure(tool_name="shell", result=billing) is False


@pytest.mark.asyncio
async def test_empty_written_delivery_reason_rejects_zero_byte_html():
    mcp = AsyncMock()
    mcp.invoke = AsyncMock(
        return_value=ToolResult(
            ok=True, output={"is_file": True, "is_dir": False, "size": 0, "path": "index.html"}
        )
    )
    proof = exe._ExecutionProof(successful_tool_calls=1, written_paths={"index.html"})
    reason = await exe._empty_written_delivery_reason(
        mcp=mcp,
        sandbox_id="sb",
        proof=proof,
        requires_artifact=True,
    )
    assert reason is not None
    assert "过小或为空" in reason


@pytest.mark.asyncio
async def test_execute_blocks_stop_after_unrecovered_shell_failure():
    sid = uuid4()
    stream = _StreamStub(
        [
            ("", [_pending("cid_1", "shell", '{"cmd":"false"}')], (8, 4)),
            ("DONE: 已经完成", [], (3, 2)),
            ("DONE: 还是完成", [], (2, 1)),
        ]
    )
    mcp = AsyncMock()
    mcp.invoke = AsyncMock(
        return_value=ToolResult(
            ok=False,
            error="command failed",
            preview="boom",
            output={"exit_code": 1, "cmd": "false"},
        )
    )

    with (
        patch.object(exe, "_stream_one_turn", stream),
        patch.object(
            exe, "maybe_compact", AsyncMock(side_effect=lambda messages, **_: (messages, False, None))
        ),
        patch.object(exe, "emit", AsyncMock()),
        patch.object(exe, "emit_model_usage", AsyncMock()),
        patch.object(exe, "get_async_openai", return_value=object()),
        patch.object(exe, "tool_manifest_cache", return_value=_shell_manifest_cache()),
        patch.object(exe, "build_system_prompt", return_value="sys"),
        patch.object(exe, "get_client", return_value=mcp),
        patch.object(exe, "get_settings", return_value=_SettingsStub()),
    ):
        state = await exe.execute_node(
            {
                "session_id": sid,
                "run_id": "r1",
                "executor_model": "agent-executor",
                "sandbox_id": str(sid),
                "user_message": "随便跑一下",
                "messages": [],
                "tool_turns": 0,
            }
        )

    assert state["finished"] is True
    assert "尚未恢复" in (state["execution_summary"].get("delivery_missing_reason") or "")
    assert int(state["execution_summary"].get("unrecovered_failures") or 0) >= 1
    assert stream.calls >= 3
    assert mcp.invoke.await_count >= 2


@pytest.mark.asyncio
async def test_execute_once_retry_recovers_timeout_and_allows_stop():
    sid = uuid4()
    stream = _StreamStub(
        [
            ("", [_pending("cid_1", "shell", '{"cmd":"echo hi"}')], (8, 4)),
            ("hello world", [], (3, 2)),
        ]
    )
    mcp = AsyncMock()
    mcp.invoke = AsyncMock(
        side_effect=[
            ToolResult(
                ok=False, error="timeout", preview="timed out", output={"timed_out": True, "cmd": "echo hi"}
            ),
            ToolResult(ok=True, preview="hi\n", output={"exit_code": 0, "cmd": "echo hi"}),
        ]
    )

    with (
        patch.object(exe, "_stream_one_turn", stream),
        patch.object(
            exe, "maybe_compact", AsyncMock(side_effect=lambda messages, **_: (messages, False, None))
        ),
        patch.object(exe, "emit", AsyncMock()),
        patch.object(exe, "emit_model_usage", AsyncMock()),
        patch.object(exe, "get_async_openai", return_value=object()),
        patch.object(exe, "tool_manifest_cache", return_value=_shell_manifest_cache()),
        patch.object(exe, "build_system_prompt", return_value="sys"),
        patch.object(exe, "get_client", return_value=mcp),
        patch.object(exe, "get_settings", return_value=_SettingsStub()),
    ):
        state = await exe.execute_node(
            {
                "session_id": sid,
                "run_id": "r1",
                "executor_model": "agent-executor",
                "sandbox_id": str(sid),
                "user_message": "说一声 hi",
                "messages": [],
                "tool_turns": 0,
            }
        )

    assert state["assistant_text"] == "hello world"
    assert state["execution_summary"]["unrecovered_failures"] == 0
    assert state["execution_summary"]["recovered_failures"] == 1
    assert mcp.invoke.await_count == 2
    assert stream.calls == 2


def test_parse_ask_user_questions_forces_custom_and_keeps_options():
    questions = exe._parse_ask_user_questions(
        {
            "prompt": "积分不够，怎么继续？",
            "options": ["充值后续", "改免费方案"],
            "allow_custom": False,
        }
    )
    assert questions[0]["prompt"] == "积分不够，怎么继续？"
    assert questions[0]["options"] == ["充值后续", "改免费方案"]
    assert questions[0]["allow_custom"] is True


def test_ask_user_rejects_fake_internal_scheduler_authorization():
    reason = exe._ask_user_internal_bypass_reason(
        {
            "prompt": "当前所有视频工具都被调度器拦截，请授权继续 TODO 3",
            "options": ["授权放行全部 wan_* 工具", "切换方案"],
        }
    )
    assert reason is not None
    assert "用户回答不会改变 TODO 或工具许可" in reason


def test_ask_user_keeps_real_business_decision_available():
    reason = exe._ask_user_internal_bypass_reason(
        {
            "prompt": "发布到公网会覆盖现有版本，是否继续？",
            "options": ["确认发布", "保留现有版本"],
        }
    )
    assert reason is None


@pytest.mark.asyncio
async def test_internal_scheduler_ask_user_is_rejected_without_pausing():
    args = '{"prompt":"调度器阻止 wan_i2v，请授权继续 TODO 3","options":["授权放行 wan_i2v","切换方案"]}'
    messages: list[Any] = []
    with (
        patch.object(exe, "emit", AsyncMock()),
        patch.object(exe, "get_client", return_value=AsyncMock()),
    ):
        proof, plan = await exe._run_tool_calls(
            [_pending("cid_internal_ask", "ask_user", args)],
            plan=None,
            sandbox_id="sandbox",
            session_id=uuid4(),
            run_id="r1",
            working_messages=messages,
            manifests=[exe.ASK_USER_MANIFEST],
        )

    assert plan is None
    assert proof.user_questions == []
    assert any(
        isinstance(message, ToolMessage) and "内部计划/调度冲突" in str(message.content)
        for message in messages
    )


def test_with_ask_user_manifest_is_always_injected():
    names = [m.name for m in exe._with_ask_user_manifest([])]
    assert names == ["ask_user"]


@pytest.mark.asyncio
async def test_execute_ask_user_pauses_for_confirmation_card():
    sid = uuid4()
    args = '{"prompt":"积分不够，怎么继续？","options":["充值后续","改免费方案"]}'
    stream = _StreamStub([("", [_pending("cid_ask", "ask_user", args)], (4, 2))])
    mcp = AsyncMock()
    mcp.invoke = AsyncMock(side_effect=AssertionError("ask_user must not hit MCP"))

    with (
        patch.object(exe, "_stream_one_turn", stream),
        patch.object(
            exe, "maybe_compact", AsyncMock(side_effect=lambda messages, **_: (messages, False, None))
        ),
        patch.object(exe, "emit", AsyncMock()),
        patch.object(exe, "emit_model_usage", AsyncMock()),
        patch.object(exe, "get_async_openai", return_value=object()),
        patch.object(exe, "tool_manifest_cache", return_value={}),
        patch.object(exe, "build_system_prompt", return_value="sys"),
        patch.object(exe, "get_client", return_value=mcp),
        patch.object(exe, "get_settings", return_value=_SettingsStub()),
        patch.object(exe, "persist_task_frame_pointer", AsyncMock(return_value=None)),
        patch.object(exe, "_emit_task_frame_ui", AsyncMock()),
        patch.object(exe, "_append_executor_progress", AsyncMock()),
    ):
        state = await exe.execute_node(
            {
                "session_id": sid,
                "run_id": "r1",
                "executor_model": "agent-executor",
                "sandbox_id": str(sid),
                "user_message": "剪一个片头",
                "messages": [],
                "tool_turns": 0,
                "task_frame": {"deliverable_type": "video", "should_invoke_planner": True},
            }
        )

    assert "error" not in state
    assert state["finished"] is True
    assert state["task_frame"]["needs_clarification"] is True
    assert state["task_frame"]["awaiting_execute_decision"] is True
    assert state["task_frame"]["execute_resume_goal"] == "剪一个片头"
    assert state["task_frame"]["clarification_questions"][0]["prompt"] == "积分不够，怎么继续？"
    mcp.invoke.assert_not_called()
    assert stream.calls == 1
