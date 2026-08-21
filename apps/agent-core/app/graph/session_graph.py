"""LangGraph session state machine.

Current graph:
  ingest → task_frame → (clarify | direct_answer | plan → execute ↔ reflect) → finalize

Context compaction stays inside execute (`maybe_compact`); it is not a graph node.
Checkpoints persist to Postgres via `langgraph-checkpoint-postgres`.
Temporal Activity retries use the same `thread_id=session_id` and resume from the
next node (`ainvoke(None)`) instead of restarting at ingest.
"""

from __future__ import annotations

from functools import lru_cache

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, START, StateGraph

from app.config import get_settings
from app.graph.nodes.execute import execute_node
from app.graph.nodes.finalize import finalize_node
from app.graph.nodes.ingest import ingest_node
from app.graph.nodes.light_reply import clarify_node, direct_answer_node
from app.graph.nodes.plan import plan_node
from app.graph.nodes.reflect import reflect_node
from app.graph.nodes.task_frame import task_frame_node
from app.graph.state import SessionState

_graph = None
_saver_ctx = None


def _route_after_execute(state: SessionState) -> str:
    return "finalize" if state.get("error") else "reflect"


def _route_after_task_frame(state: SessionState) -> str:
    if state.get("error"):
        return "finalize"
    frame = state.get("task_frame") or {}
    if frame.get("needs_clarification"):
        return "clarify"
    if not frame.get("should_invoke_planner", True):
        return "direct_answer"
    return "plan"


def _route_after_reflect(state: SessionState) -> str:
    nxt = state.get("next_node")
    if nxt in {"execute", "plan", "finalize"}:
        return nxt
    return "finalize"


def build_graph() -> StateGraph:
    g: StateGraph = StateGraph(SessionState)
    g.add_node("ingest", ingest_node)
    g.add_node("task_frame", task_frame_node)
    g.add_node("clarify", clarify_node)
    g.add_node("direct_answer", direct_answer_node)
    g.add_node("plan", plan_node)
    g.add_node("execute", execute_node)
    g.add_node("reflect", reflect_node)
    g.add_node("finalize", finalize_node)
    g.add_edge(START, "ingest")
    g.add_edge("ingest", "task_frame")
    g.add_conditional_edges(
        "task_frame",
        _route_after_task_frame,
        {
            "clarify": "clarify",
            "direct_answer": "direct_answer",
            "plan": "plan",
            "finalize": "finalize",
        },
    )
    g.add_edge("clarify", "finalize")
    g.add_edge("direct_answer", "finalize")
    g.add_edge("plan", "execute")
    g.add_conditional_edges(
        "execute",
        _route_after_execute,
        {"reflect": "reflect", "finalize": "finalize"},
    )
    g.add_conditional_edges(
        "reflect",
        _route_after_reflect,
        {"execute": "execute", "plan": "plan", "finalize": "finalize"},
    )
    g.add_edge("finalize", END)
    return g


@lru_cache
def _postgres_conn_string() -> str:
    """LangGraph Postgres checkpoint wants a libpq-style URL (not asyncpg-specific)."""
    url = get_settings().postgres_url
    # Accept either 'postgresql://', 'postgresql+asyncpg://', 'postgres://' and normalise
    return (
        url.replace("postgresql+asyncpg://", "postgresql://")
        .replace("postgres://", "postgresql://")
    )


async def get_compiled_graph():
    """Return a compiled graph with Postgres checkpointer.

    We keep a single long-lived saver for the process. `AsyncPostgresSaver.from_conn_string`
    returns a context manager; we enter it once at startup.
    """
    global _graph, _saver_ctx
    if _graph is not None:
        return _graph

    _saver_ctx = AsyncPostgresSaver.from_conn_string(_postgres_conn_string())
    saver = await _saver_ctx.__aenter__()
    await saver.setup()
    _graph = build_graph().compile(checkpointer=saver)
    return _graph


async def close_graph() -> None:
    global _graph, _saver_ctx
    if _saver_ctx is not None:
        await _saver_ctx.__aexit__(None, None, None)
    _graph = None
    _saver_ctx = None
