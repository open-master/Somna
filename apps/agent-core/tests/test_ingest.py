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
