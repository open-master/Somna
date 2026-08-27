from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.graph.nodes import ingest as ingest_mod


@pytest.mark.asyncio
async def test_ingest_uses_stable_message_id_for_temporal_retry():
    sid = uuid4()
    client = SimpleNamespace(ensure_sandbox=AsyncMock())

    with (
        patch.object(ingest_mod, "get_client", return_value=client),
        patch.object(ingest_mod, "emit", AsyncMock()),
    ):
        first = await ingest_mod.ingest_node(
            {
                "session_id": sid,
                "run_id": "run_same",
                "user_message": "继续任务",
                "attachments": [],
                "messages": [],
            }
        )
        retried = await ingest_mod.ingest_node(
            {
                "session_id": sid,
                "run_id": "run_same",
                "user_message": "继续任务",
                "attachments": [],
                "messages": [],
            }
        )

    assert first["messages"][-1].id == "user:run_same"
    assert retried["messages"][-1].id == first["messages"][-1].id


@pytest.mark.asyncio
async def test_ingest_resets_compact_memory_for_new_user_turn():
    sid = uuid4()
    client = SimpleNamespace(ensure_sandbox=AsyncMock())

    with (
        patch.object(ingest_mod, "get_client", return_value=client),
        patch.object(ingest_mod, "emit", AsyncMock()),
    ):
        out = await ingest_mod.ingest_node(
            {
                "session_id": sid,
                "run_id": "run_new",
                "user_message": "换一个任务",
                "attachments": [],
                "messages": [],
                "compact_memory": "上一轮已经生成过词云。",
                "execution_summary": {"written_paths": ["/workspace/old.png"]},
                "plan": {"run_id": "run_old", "todos": [{"id": "1", "status": "done"}]},
                "plan_path": ".somna/runs/run_old/plan.json",
            }
        )

    assert out["compact_memory"] is None
    assert out["execution_summary"] is None
    assert out["plan"] is None
    assert out["plan_path"] is None
    assert out["tool_turns"] == 0
    assert out["resume_execute"] is False


@pytest.mark.asyncio
async def test_ingest_resume_execute_keeps_plan_and_proof():
    sid = uuid4()
    client = SimpleNamespace(ensure_sandbox=AsyncMock())
    plan = {"todos": [{"id": "1", "text": "剪片头", "status": "in_progress"}]}
    summary = {"written_paths": ["intro.mp4"], "successful_tool_calls": 2}

    with (
        patch.object(ingest_mod, "get_client", return_value=client),
        patch.object(ingest_mod, "emit", AsyncMock()),
    ):
        out = await ingest_mod.ingest_node(
            {
                "session_id": sid,
                "run_id": "run_resume",
                "user_message": "针对你的确认，我的选择如下：\n1. 怎么继续？\n回答：改免费方案",
                "attachments": [],
                "messages": [],
                "task_frame": {
                    "awaiting_execute_decision": True,
                    "execute_resume_goal": "剪一个片头",
                    "needs_clarification": True,
                },
                "plan": plan,
                "execution_summary": summary,
                "compact_memory": "已准备素材",
            }
        )

    assert out["resume_execute"] is True
    assert out["resume_goal"] == "剪一个片头"
    assert out["plan"] == plan
    assert out["execution_summary"] == summary
    assert out["compact_memory"] == "已准备素材"
