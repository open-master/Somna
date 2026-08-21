from __future__ import annotations

from typing import TypedDict
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from app.graph.resume import checkpoint_resume_action, invoke_session_graph


def test_checkpoint_start_when_empty():
    assert checkpoint_resume_action(None, "run_1") == "start"
    assert checkpoint_resume_action(SimpleNamespace(values={}, next=("ingest",)), "run_1") == "start"


def test_checkpoint_start_when_different_run():
    snap = SimpleNamespace(
        values={"run_id": "run_old", "tool_turns": 4, "plan": {"todos": []}},
        next=(),
    )
    assert checkpoint_resume_action(snap, "run_new") == "start"


def test_checkpoint_resume_when_same_run_in_progress():
    snap = SimpleNamespace(
        values={"run_id": "run_1", "tool_turns": 12, "plan": {"todos": [{"id": "t1"}]}},
        next=("execute",),
    )
    assert checkpoint_resume_action(snap, "run_1") == "resume"


def test_checkpoint_skip_when_same_run_already_finished():
    snap = SimpleNamespace(
        values={"run_id": "run_1", "finished": True},
        next=(),
    )
    assert checkpoint_resume_action(snap, "run_1") == "skip"


@pytest.mark.asyncio
async def test_invoke_session_graph_resumes_without_reset_payload():
    sid = uuid4()
    graph = SimpleNamespace(
        aget_state=AsyncMock(
            return_value=SimpleNamespace(
                values={"run_id": "run_1", "tool_turns": 12, "plan": {"id": "p1"}},
                next=("execute",),
            )
        ),
        ainvoke=AsyncMock(),
    )
    payload = {"run_id": "run_1", "messages": [], "tool_turns": 0, "plan": None}

    action = await invoke_session_graph(
        graph=graph,
        payload=payload,
        session_id=sid,
        run_id="run_1",
    )

    assert action == "resume"
    graph.ainvoke.assert_awaited_once()
    args, kwargs = graph.ainvoke.await_args
    assert args[0] is None
    assert kwargs["config"]["configurable"]["thread_id"] == str(sid)


@pytest.mark.asyncio
async def test_invoke_session_graph_starts_new_run_with_payload():
    sid = uuid4()
    graph = SimpleNamespace(
        aget_state=AsyncMock(
            return_value=SimpleNamespace(
                values={"run_id": "run_old", "finished": True},
                next=(),
            )
        ),
        ainvoke=AsyncMock(),
    )
    payload = {"run_id": "run_new", "messages": [], "tool_turns": 0}

    action = await invoke_session_graph(
        graph=graph,
        payload=payload,
        session_id=sid,
        run_id="run_new",
    )

    assert action == "start"
    graph.ainvoke.assert_awaited_once()
    args, kwargs = graph.ainvoke.await_args
    assert args[0] is payload
    assert kwargs["config"]["configurable"]["thread_id"] == str(sid)


@pytest.mark.asyncio
async def test_invoke_session_graph_skips_completed_run():
    sid = uuid4()
    graph = SimpleNamespace(
        aget_state=AsyncMock(
            return_value=SimpleNamespace(
                values={"run_id": "run_1", "finished": True, "assistant_text": "done"},
                next=(),
            )
        ),
        ainvoke=AsyncMock(),
    )

    action = await invoke_session_graph(
        graph=graph,
        payload={"run_id": "run_1", "tool_turns": 0},
        session_id=sid,
        run_id="run_1",
    )

    assert action == "skip"
    graph.ainvoke.assert_not_called()


class _TinyState(TypedDict, total=False):
    run_id: str
    seen: list[str]


def _tiny_graph(hits: list[str]):
    async def first(state: _TinyState) -> dict:
        hits.append("first")
        return {"seen": list(state.get("seen") or []) + ["first"]}

    async def second(state: _TinyState) -> dict:
        hits.append("second")
        if hits.count("second") == 1:
            raise RuntimeError("boom")
        return {"seen": list(state.get("seen") or []) + ["second"]}

    g = StateGraph(_TinyState)
    g.add_node("first", first)
    g.add_node("second", second)
    g.add_edge(START, "first")
    g.add_edge("first", "second")
    g.add_edge("second", END)
    return g.compile(checkpointer=MemorySaver())


@pytest.mark.asyncio
async def test_memory_checkpointer_resume_does_not_rerun_completed_nodes():
    hits: list[str] = []
    graph = _tiny_graph(hits)
    sid = uuid4()
    payload = {"run_id": "run_1", "seen": []}

    with pytest.raises(RuntimeError, match="boom"):
        await invoke_session_graph(
            graph=graph,
            payload=payload,
            session_id=sid,
            run_id="run_1",
        )
    assert hits == ["first", "second"]

    action = await invoke_session_graph(
        graph=graph,
        payload={"run_id": "run_1", "seen": []},
        session_id=sid,
        run_id="run_1",
    )
    assert action == "resume"
    assert hits == ["first", "second", "second"]

    skip = await invoke_session_graph(
        graph=graph,
        payload={"run_id": "run_1", "seen": []},
        session_id=sid,
        run_id="run_1",
    )
    assert skip == "skip"
    assert hits == ["first", "second", "second"]

    start = await invoke_session_graph(
        graph=graph,
        payload={"run_id": "run_2", "seen": []},
        session_id=sid,
        run_id="run_2",
    )
    assert start == "start"
    assert hits[-2:] == ["first", "second"]
