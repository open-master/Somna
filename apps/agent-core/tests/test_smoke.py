"""Smoke tests — do not require a running LiteLLM.

Full integration (graph → LiteLLM → Kimi) is exercised via
`docker compose up agent-core` + `tests/integration/*` (added later).
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.graph.session_graph import build_graph
from app.graph.state import SessionState


def test_build_graph_has_expected_nodes():
    g = build_graph()
    compiled = g.compile()
    nodes = set(compiled.get_graph().nodes.keys())
    # LangGraph adds __start__ / __end__ internally
    assert {
        "ingest",
        "task_frame",
        "clarify",
        "direct_answer",
        "plan",
        "execute",
        "reflect",
        "invalid_state",
        "finalize",
    } <= nodes


def test_session_state_typing_accepts_minimum_fields():
    sid = uuid4()
    state: SessionState = {
        "session_id": sid,
        "run_id": "r1",
        "user_message": "hello",
        "executor_model": "agent-executor",
        "messages": [],
        "finished": False,
        "error": None,
    }
    assert state["session_id"] == sid


@pytest.mark.asyncio
async def test_event_models_are_importable():
    from somna_events import MessageDeltaEvent, SessionPhase, StatusEvent

    e1 = MessageDeltaEvent(session_id=uuid4(), text="hi")
    assert e1.type == "message.delta"
    e2 = StatusEvent(session_id=uuid4(), phase=SessionPhase.executing)
    assert e2.phase == "executing"
