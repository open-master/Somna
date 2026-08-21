from __future__ import annotations

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.graph.nodes import finalize as fin


@pytest.mark.asyncio
async def test_finalize_writes_memory_for_successful_run():
    sid = uuid4()
    add_memory = AsyncMock(return_value=True)
    closed = AsyncMock()

    with (
        patch.object(fin, "emit", AsyncMock()),
        patch.object(fin, "mark_session_run_closed", closed),
        patch.object(fin, "memory_enabled", return_value=True),
        patch.object(fin, "add_memory", add_memory),
    ):
        out = await fin.finalize_node(
            {
                "session_id": sid,
                "run_id": "r1",
                "user_message": "我更喜欢深色主题",
                "assistant_text": "后续我会优先按深色风格输出。",
                "error": None,
            }
        )

    assert out == {}
    add_memory.assert_awaited_once()
    payload = add_memory.await_args.args[0]
    assert "我更喜欢深色主题" in payload
    assert "深色风格" in payload
    closed.assert_awaited_once_with(sid, status="active", last_phase="done")


@pytest.mark.asyncio
async def test_finalize_skips_memory_write_on_error():
    sid = uuid4()
    add_memory = AsyncMock(return_value=True)
    closed = AsyncMock()

    with (
        patch.object(fin, "emit", AsyncMock()),
        patch.object(fin, "mark_session_run_closed", closed),
        patch.object(fin, "memory_enabled", return_value=True),
        patch.object(fin, "add_memory", add_memory),
    ):
        out = await fin.finalize_node(
            {
                "session_id": sid,
                "run_id": "r1",
                "user_message": "帮我生成报告",
                "assistant_text": "",
                "error": "boom",
            }
        )

    assert out == {}
    add_memory.assert_not_awaited()
    closed.assert_awaited_once_with(sid, status="error", last_phase="error")


@pytest.mark.asyncio
async def test_finalize_emits_waiting_user_after_clarify_path():
    """追问结束后应处于「等待用户」，不应发 done（避免顶栏「已完成」误导）。"""
    from somna_events import SessionPhase

    sid = uuid4()
    emit = AsyncMock()
    closed = AsyncMock()

    with (
        patch.object(fin, "emit", emit),
        patch.object(fin, "mark_session_run_closed", closed),
        patch.object(fin, "memory_enabled", return_value=False),
    ):
        await fin.finalize_node(
            {
                "session_id": sid,
                "run_id": "r1",
                "user_message": "你来决定",
                "assistant_text": "请先回答：…",
                "error": None,
                "task_frame": {"needs_clarification": True},
            }
        )

    status_events = [c.args[0] for c in emit.await_args_list if c.args[0].type == "status"]
    assert status_events and status_events[-1].phase == SessionPhase.waiting_user
    assert "补充" in (status_events[-1].message or "")
    closed.assert_awaited_once_with(sid, status="active", last_phase="waiting_user")


@pytest.mark.asyncio
async def test_finalize_does_not_store_clarification_as_long_term_memory():
    sid = uuid4()
    add_memory = AsyncMock(return_value=True)

    with (
        patch.object(fin, "emit", AsyncMock()),
        patch.object(fin, "mark_session_run_closed", AsyncMock()),
        patch.object(fin, "memory_enabled", return_value=True),
        patch.object(fin, "add_memory", add_memory),
    ):
        await fin.finalize_node(
            {
                "session_id": sid,
                "run_id": "r1",
                "user_message": "帮我生成报告",
                "assistant_text": "请补充报告范围和格式。",
                "error": None,
                "task_frame": {"needs_clarification": True},
            }
        )

    add_memory.assert_not_awaited()


@pytest.mark.asyncio
async def test_finalize_emits_partial_and_skips_memory_for_unfinished_plan():
    from somna_events import SessionPhase

    sid = uuid4()
    emit = AsyncMock()
    add_memory = AsyncMock(return_value=True)
    settle = AsyncMock()
    closed = AsyncMock()

    with (
        patch.object(fin, "emit", emit),
        patch.object(fin, "mark_session_run_closed", closed),
        patch.object(fin, "memory_enabled", return_value=True),
        patch.object(fin, "add_memory", add_memory),
        patch.object(fin, "settle_billing_run", settle),
    ):
        await fin.finalize_node(
            {
                "session_id": sid,
                "run_id": "r1",
                "user_id": "u1",
                "user_message": "生成报告",
                "assistant_text": "完成了可完成的部分。",
                "error": None,
                "task_frame": {"needs_clarification": False},
                "plan": {
                    "todos": [
                        {"id": "1", "text": "收集资料", "status": "done"},
                        {"id": "2", "text": "生成报告", "status": "failed"},
                    ]
                },
            }
        )

    status_events = [c.args[0] for c in emit.await_args_list if c.args[0].type == "status"]
    assert status_events[-1].phase == SessionPhase.partial
    add_memory.assert_not_awaited()
    settle.assert_awaited_once_with(run_id="r1", outcome="partial")
    closed.assert_awaited_once_with(sid, status="active", last_phase="partial")
