"""Emitter tests."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.events import emitter


@pytest.mark.asyncio
async def test_fetch_history_preserves_db_seq_over_payload_seq():
    conn = AsyncMock()
    conn.fetch = AsyncMock(
        return_value=[
            {
                "seq": 42,
                "payload": {
                    "seq": None,
                    "type": "status",
                    "message": "hello",
                },
            }
        ]
    )
    acquire_cm = AsyncMock()
    acquire_cm.__aenter__.return_value = conn
    acquire_cm.__aexit__.return_value = None
    pool = SimpleNamespace(acquire=lambda: acquire_cm)

    with patch.object(emitter, "get_pool", return_value=pool):
        rows = await emitter.fetch_history("00000000-0000-0000-0000-000000000000")

    assert rows == [{"seq": 42, "type": "status", "message": "hello"}]
