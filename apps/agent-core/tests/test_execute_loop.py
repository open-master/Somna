"""Unit test for the execute node's tool-calling loop.

Streaming and HTTP are patched so the loop logic is exercised in isolation.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from uuid import uuid4
from unittest.mock import AsyncMock, patch

import pytest

from app.graph.nodes import execute as exe
from app.tools.client import ToolManifest, ToolResult


class _SettingsStub:
    agent_max_turns = 40
    agent_default_executor = "agent-executor"
    agent_default_coder = "agent-coder"


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


# --- tests ---


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
async def test_text_only_turn_finishes_without_tool_calls():
    sid = uuid4()
    stream = _StreamStub([("hello world", [], (10, 5))])

    emitted: list[Any] = []

    async def _emit(ev):
        emitted.append(ev)

    with (
        patch.object(exe, "_stream_one_turn", stream),
        patch.object(exe, "maybe_compact", AsyncMock(side_effect=lambda messages, **_: (messages, False, None))),
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
    assert stream.calls == 1
    # token.usage fired
    assert any(type(e).__name__ == "TokenUsageEvent" for e in emitted)


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
        patch.object(exe, "maybe_compact", AsyncMock(side_effect=lambda messages, **_: (messages, False, None))),
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
        patch.object(exe, "maybe_compact", AsyncMock(side_effect=lambda messages, **_: (messages, False, None))),
        patch.object(exe, "emit", _emit),
        patch.object(exe, "get_async_openai", return_value=object()),
        patch.object(exe, "tool_manifest_cache", return_value={}),
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

    with (
        patch.object(exe, "_stream_one_turn", _stream),
        patch.object(exe, "maybe_compact", AsyncMock(side_effect=lambda messages, **_: (messages, False, None))),
        patch.object(exe, "emit", _emit),
        patch.object(exe, "get_async_openai", return_value=object()),
        patch.object(exe, "tool_manifest_cache", return_value={}),
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
    assert "最大工具" in state["assistant_text"]
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
        patch.object(exe, "maybe_compact", AsyncMock(side_effect=lambda messages, **_: (messages, False, None))),
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
    assert "未检测到真实交付证据" in state["error"]


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
        "shell": ToolManifest(name="shell", description="", input_schema={"type": "object", "properties": {}}, mutates=True),
        "filesystem": ToolManifest(
            name="filesystem", description="", input_schema={"type": "object", "properties": {}}, mutates=True
        ),
    }

    with (
        patch.object(exe, "_stream_one_turn", stream),
        patch.object(exe, "maybe_compact", AsyncMock(side_effect=lambda messages, **_: (messages, False, None))),
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
    assert "未检测到真实交付证据" in state["error"]


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
        "shell": ToolManifest(name="shell", description="", input_schema={"type": "object", "properties": {}}, mutates=True),
    }

    with (
        patch.object(exe, "_stream_one_turn", stream),
        patch.object(exe, "maybe_compact", AsyncMock(side_effect=lambda messages, **_: (messages, False, None))),
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
        patch.object(exe, "maybe_compact", AsyncMock(side_effect=lambda messages, **_: (messages, False, None))),
        patch.object(exe, "emit", AsyncMock()),
        patch.object(exe, "get_async_openai", return_value=object()),
        patch.object(exe, "tool_manifest_cache", return_value={}),
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
                output={"exit_code": 0, "stdout": "installed", "cmd": "if [ -f package.json ]; then npm install; else exit 1; fi"},
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
        patch.object(exe, "maybe_compact", AsyncMock(side_effect=lambda messages, **_: (messages, False, None))),
        patch.object(exe, "emit", AsyncMock()),
        patch.object(exe, "get_async_openai", return_value=object()),
        patch.object(exe, "tool_manifest_cache", return_value={}),
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
            manifest=None,
            mcp_tool_models={"visual_critique": "qwen3-vl-flash"},
        )
    assert mcp.invoke.await_count == 1
    kw = mcp.invoke.await_args.kwargs
    assert kw["args"]["model"] == "qwen3-vl-flash"


@pytest.mark.asyncio
async def test_invoke_tool_visual_critique_respects_explicit_model():
    mcp = AsyncMock()
    mcp.invoke = AsyncMock(return_value=ToolResult(ok=True, preview="ok"))
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
            manifest=None,
            mcp_tool_models={"visual_critique": "qwen3-vl-flash"},
        )
    assert mcp.invoke.await_args.kwargs["args"]["model"] == "custom-vl"
