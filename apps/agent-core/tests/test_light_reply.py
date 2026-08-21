from __future__ import annotations

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from somna_events import MessageDeltaEvent

from app.graph.nodes import light_reply as light_reply_mod


@pytest.mark.asyncio
async def test_clarify_skips_message_delta_when_card_questions_exist():
    sid = uuid4()
    emit = AsyncMock()

    with patch.object(light_reply_mod, "emit", emit):
        out = await light_reply_mod.clarify_node(
            {
                "session_id": sid,
                "run_id": "run_q",
                "task_frame": {
                    "needs_clarification": True,
                    "clarification_questions": [
                        {
                            "id": "q1",
                            "prompt": "要什么格式？",
                            "options": ["PDF", "PPT"],
                            "allow_custom": True,
                        }
                    ],
                },
                "messages": [],
            }
        )

    emitted_types = [call.args[0].type for call in emit.await_args_list]
    assert "status" in emitted_types
    assert "message.delta" not in emitted_types
    assert out["finished"] is True
    assert "要什么格式？" in out["assistant_text"]


@pytest.mark.asyncio
async def test_clarify_emits_message_delta_when_no_card_questions():
    sid = uuid4()
    emit = AsyncMock()

    with patch.object(light_reply_mod, "emit", emit):
        await light_reply_mod.clarify_node(
            {
                "session_id": sid,
                "run_id": "run_plain",
                "task_frame": {"needs_clarification": True, "clarification_questions": []},
                "messages": [],
            }
        )

    deltas = [call.args[0] for call in emit.await_args_list if isinstance(call.args[0], MessageDeltaEvent)]
    assert len(deltas) == 1
    assert "补充" in deltas[0].text
