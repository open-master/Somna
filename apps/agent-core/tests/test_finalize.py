from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.graph.nodes import finalize as fin


class _Conn:
    async def execute(self, *_args, **_kwargs):
        return None


class _Acquire:
    async def __aenter__(self):
        return _Conn()

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _Pool:
    def acquire(self):
        return _Acquire()


@pytest.mark.asyncio
async def test_finalize_writes_memory_for_successful_run():
    sid = uuid4()
    add_memory = AsyncMock(return_value=True)

    with (
        patch.object(fin, "emit", AsyncMock()),
        patch.object(fin, "get_pool", return_value=_Pool()),
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


@pytest.mark.asyncio
async def test_finalize_skips_memory_write_on_error():
    sid = uuid4()
    add_memory = AsyncMock(return_value=True)

    with (
        patch.object(fin, "emit", AsyncMock()),
        patch.object(fin, "get_pool", return_value=_Pool()),
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
