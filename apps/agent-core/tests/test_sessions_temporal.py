from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.api import sessions as sessions_mod


class _Conn:
    def __init__(self, rows):
        self._rows = list(rows)
        self.execute = AsyncMock()

    async def fetchrow(self, *_args, **_kwargs):
        if not self._rows:
            return None
        return self._rows.pop(0)


class _Acquire:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _Pool:
    def __init__(self, conn):
        self._conn = conn

    def acquire(self):
        return _Acquire(self._conn)


class _TemporalClient:
    def __init__(self):
        self.start_workflow = AsyncMock()
        self._handle = SimpleNamespace(cancel=AsyncMock())

    def get_workflow_handle(self, _workflow_id: str):
        return self._handle


class _SettingsStub:
    agent_default_planner = "agent-planner"
    agent_default_executor = "agent-executor"
    temporal_task_queue = "somna-agent-core"


@pytest.mark.asyncio
async def test_post_message_starts_temporal_workflow():
    sid = uuid4()
    conn = _Conn(
        [
            {
                "planner_model": "agent-planner",
                "executor_model": "agent-executor",
                "status": "active",
            }
        ]
    )
    temporal = _TemporalClient()

    with (
        patch.object(sessions_mod, "get_pool", return_value=_Pool(conn)),
        patch.object(sessions_mod, "get_temporal_client", AsyncMock(return_value=temporal)),
        patch.object(sessions_mod, "get_settings", return_value=_SettingsStub()),
    ):
        out = await sessions_mod.post_message(
            sid,
            sessions_mod.PostMessageReq(text="继续执行", attachments=[]),
        )

    assert out.run_id.startswith("run_")
    temporal.start_workflow.assert_awaited_once()
    wf_arg = temporal.start_workflow.call_args[0][1]
    assert getattr(wf_arg, "executor_engine", None) == "native"
    assert conn.execute.await_count == 2


@pytest.mark.asyncio
async def test_post_message_passes_executor_engine_anthropic():
    sid = uuid4()
    conn = _Conn([{"planner_model": None, "executor_model": None, "status": "active"}])
    temporal = _TemporalClient()
    with (
        patch.object(sessions_mod, "get_pool", return_value=_Pool(conn)),
        patch.object(sessions_mod, "get_temporal_client", AsyncMock(return_value=temporal)),
        patch.object(sessions_mod, "get_settings", return_value=_SettingsStub()),
    ):
        await sessions_mod.post_message(
            sid,
            sessions_mod.PostMessageReq(text="hi", executor_engine="anthropic"),
        )
    wf_arg = temporal.start_workflow.call_args[0][1]
    assert wf_arg.executor_engine == "anthropic"


@pytest.mark.asyncio
async def test_post_message_invalid_engine_falls_back_native():
    sid = uuid4()
    conn = _Conn([{"planner_model": None, "executor_model": None, "status": "active"}])
    temporal = _TemporalClient()
    with (
        patch.object(sessions_mod, "get_pool", return_value=_Pool(conn)),
        patch.object(sessions_mod, "get_temporal_client", AsyncMock(return_value=temporal)),
        patch.object(sessions_mod, "get_settings", return_value=_SettingsStub()),
    ):
        await sessions_mod.post_message(
            sid,
            sessions_mod.PostMessageReq(text="hi", executor_engine="not-a-mode"),
        )
    wf_arg = temporal.start_workflow.call_args[0][1]
    assert wf_arg.executor_engine == "native"


@pytest.mark.asyncio
async def test_interrupt_session_cancels_temporal_workflow():
    sid = uuid4()
    conn = _Conn(
        [
            {
                "workflow_id": "session:1:run:2",
                "run_id": "run_123",
                "status": "running",
            }
        ]
    )
    temporal = _TemporalClient()

    with (
        patch.object(sessions_mod, "get_pool", return_value=_Pool(conn)),
        patch.object(sessions_mod, "get_temporal_client", AsyncMock(return_value=temporal)),
        patch.object(sessions_mod.get_registry(), "set_reason", AsyncMock(return_value="run_123")),
    ):
        out = await sessions_mod.interrupt_session(sid, reason="user_stop")

    assert out.interrupted is True
    assert out.run_id == "run_123"
    temporal._handle.cancel.assert_awaited_once()
