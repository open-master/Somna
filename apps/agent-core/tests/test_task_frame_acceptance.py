"""验收：任务定调（阶段 A）路由与边界行为（不调用真实 LLM）。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.graph.nodes import task_frame as tf_mod
from app.graph.session_graph import _route_after_task_frame
from app.graph.state import SessionState


def test_route_clarify_wins_over_planner_flag():
    state: SessionState = {
        "task_frame": {
            "needs_clarification": True,
            "should_invoke_planner": True,
            "clarification_questions": ["要什么格式？"],
        }
    }
    assert _route_after_task_frame(state) == "clarify"


def test_route_direct_answer_when_no_clarify_and_no_planner():
    state: SessionState = {
        "task_frame": {
            "needs_clarification": False,
            "should_invoke_planner": False,
        }
    }
    assert _route_after_task_frame(state) == "direct_answer"


def test_route_plan_when_full_pipeline():
    state: SessionState = {
        "task_frame": {
            "needs_clarification": False,
            "should_invoke_planner": True,
        }
    }
    assert _route_after_task_frame(state) == "plan"


def test_route_finalize_on_error():
    state: SessionState = {"error": "boom", "task_frame": {"needs_clarification": False}}
    assert _route_after_task_frame(state) == "finalize"


def test_normalize_forces_no_planner_when_clarify():
    parsed = {
        "needs_clarification": True,
        "should_invoke_planner": True,
        "clarification_questions": ["范围？"],
        "reasoning_summary": "x",
    }
    out = tf_mod.normalize_task_frame(parsed)
    assert out["needs_clarification"] is True
    assert out["should_invoke_planner"] is False


def test_blank_frame_is_clarify_only():
    frame = tf_mod.frame_for_blank_user_message()
    assert frame["needs_clarification"] is True
    assert frame["should_invoke_planner"] is False
    assert frame["clarification_questions"]


@pytest.mark.asyncio
async def test_task_frame_blank_user_skips_llm():
    sid = uuid4()
    create = AsyncMock()
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create)),
    )

    with (
        patch.object(tf_mod, "get_async_openai", return_value=client),
        patch.object(tf_mod, "emit", AsyncMock()),
    ):
        out = await tf_mod.task_frame_node(
            {
                "session_id": sid,
                "run_id": "r1",
                "user_message": "   \n",
                "skip_planner": False,
            }
        )

    create.assert_not_called()
    assert out["task_frame"]["reasoning_summary"] == tf_mod._BLANK_USER_REASON
    assert out["task_frame"]["needs_clarification"] is True


@pytest.mark.asyncio
async def test_skip_planner_runs_after_blank_would_not_apply():
    """skip_planner 不应吞掉空消息：空消息应先短路为追问。"""
    sid = uuid4()

    with patch.object(tf_mod, "emit", AsyncMock()):
        out = await tf_mod.task_frame_node(
            {
                "session_id": sid,
                "run_id": "r1",
                "user_message": "",
                "skip_planner": True,
            }
        )

    frame = out["task_frame"]
    assert frame["needs_clarification"] is True
    assert frame["reasoning_summary"] == tf_mod._BLANK_USER_REASON


def test_coerce_simple_who_question_to_direct():
    frame = tf_mod.normalize_task_frame(
        {"needs_clarification": False, "should_invoke_planner": True, "reasoning_summary": "误判"}
    )
    tf_mod._maybe_coerce_simple_definitional_qa("乔布斯是谁？", frame)
    assert frame["should_invoke_planner"] is False
    assert frame["task_mode"] == "direct_answer"
    assert "coerced_simple_definitional_QA" in frame["reasoning_summary"]


def test_coerce_skips_when_research_keywords():
    frame = tf_mod.normalize_task_frame(
        {"needs_clarification": False, "should_invoke_planner": True, "reasoning_summary": "x"}
    )
    tf_mod._maybe_coerce_simple_definitional_qa("搜索一下乔布斯是谁？", frame)
    assert frame["should_invoke_planner"] is True
