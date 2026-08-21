"""验收：任务定调（阶段 A）路由与边界行为（不调用真实 LLM）。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.graph.nodes import task_frame as tf_mod
from app.graph.session_graph import _route_after_execute, _route_after_task_frame
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


def test_route_resume_execute_skips_plan_and_clarify():
    state: SessionState = {
        "resume_execute": True,
        "task_frame": {
            "needs_clarification": True,
            "should_invoke_planner": True,
        },
    }
    assert _route_after_task_frame(state) == "execute"


def test_route_after_execute_waiting_user_skips_reflect():
    assert _route_after_execute({"task_frame": {"needs_clarification": True}}) == "finalize"
    assert _route_after_execute({"task_frame": {"needs_clarification": False}}) == "reflect"


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
    assert out["clarification_questions"][0]["prompt"] == "范围？"


def test_normalize_structured_clarification_questions():
    out = tf_mod.normalize_task_frame(
        {
            "needs_clarification": True,
            "clarification_questions": [
                {
                    "id": "format",
                    "prompt": "希望交付什么格式？",
                    "options": ["Markdown", "PDF"],
                    "allow_custom": True,
                }
            ],
        }
    )
    assert out["clarification_questions"] == [
        {
            "id": "format",
            "prompt": "希望交付什么格式？",
            "options": ["Markdown", "PDF"],
            "allow_custom": True,
        }
    ]


def test_normalize_autonomy_level_invalid_keeps_default():
    out = tf_mod.normalize_task_frame({"autonomy_level": "nope", "reasoning_summary": "x"})
    assert out["autonomy_level"] == "medium"
    out_ok = tf_mod.normalize_task_frame({"autonomy_level": "low"})
    assert out_ok["autonomy_level"] == "low"


def test_normalize_billing_fields_rejects_untrusted_enums():
    out = tf_mod.normalize_task_frame(
        {
            "task_mode": "free_unlimited",
            "effort_level": "infinite",
            "deliverable_type": "mystery",
        }
    )
    assert out["task_mode"] == "full_pipeline"
    assert out["effort_level"] == "medium"
    assert out["deliverable_type"] == "unspecified"


def test_blank_frame_is_clarify_only():
    frame = tf_mod.frame_for_blank_user_message()
    assert frame["needs_clarification"] is True
    assert frame["should_invoke_planner"] is False
    assert frame["clarification_questions"]


def test_typed_delivery_gap_rejects_clips_when_compose_required():
    frame = {"deliverable_type": "video", "success_criteria": ["交付最终成片"]}
    assert tf_mod.task_expects_composed_media("做纪录片", None, frame) is True
    gap = tf_mod.typed_delivery_gap(
        paths=["artifacts/wan_t2v_closing_shot.mp4"],
        deliverable_type="video",
        composed_media_required=True,
    )
    assert gap is not None
    assert tf_mod.typed_delivery_gap(
        paths=["artifacts/final_documentary.mp4"],
        deliverable_type="video",
        composed_media_required=True,
    ) is None


def test_typed_delivery_gap_website_requires_html():
    gap = tf_mod.typed_delivery_gap(
        paths=["notes.txt"],
        deliverable_type="website",
    )
    assert gap is not None
    assert "网页" in gap
    assert (
        tf_mod.typed_delivery_gap(
            paths=["index.html"],
            deliverable_type="website",
        )
        is None
    )


def test_framing_context_excludes_current_human_turn():
    from langchain_core.messages import AIMessage, HumanMessage

    msgs = [
        HumanMessage(content="为乔布斯和盖茨做词云"),
        AIMessage(content="需要确认：1. 素材 2. 风格 3. 用途"),
        HumanMessage(content="你来决定"),
    ]
    prior = tf_mod._prior_messages_for_framing(msgs)
    assert len(prior) == 2
    ctx = tf_mod.format_conversation_context_for_framing(prior)
    assert "词云" in ctx
    assert "你来决定" not in ctx


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


def test_format_task_frame_ui_summary_for_execute_pause():
    summary = tf_mod.format_task_frame_ui_summary(
        {
            "awaiting_execute_decision": True,
            "needs_clarification": True,
            "reasoning_summary": "执行中等用户选择",
        }
    )
    assert "执行中" in summary


@pytest.mark.asyncio
async def test_task_frame_resume_execute_skips_llm():
    sid = uuid4()
    create = AsyncMock()
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    with (
        patch.object(tf_mod, "get_async_openai", return_value=client),
        patch.object(tf_mod, "emit", AsyncMock()),
        patch.object(tf_mod, "persist_task_frame_pointer", AsyncMock(return_value="p")),
        patch.object(tf_mod, "_emit_task_frame_ui", AsyncMock()),
        patch.object(tf_mod, "_authorize_frame_billing", AsyncMock(return_value=None)),
    ):
        out = await tf_mod.task_frame_node(
            {
                "session_id": sid,
                "run_id": "r2",
                "user_message": "针对你的确认，我的选择如下：\n回答：改免费方案",
                "resume_execute": True,
                "task_frame": {
                    "needs_clarification": True,
                    "awaiting_execute_decision": True,
                    "clarification_questions": [{"id": "q1", "prompt": "怎么继续？", "options": ["A"]}],
                    "deliverable_type": "video",
                    "should_invoke_planner": True,
                    "execute_resume_goal": "剪一个片头",
                },
            }
        )

    create.assert_not_called()
    assert out["resume_execute"] is True
    assert out["task_frame"]["needs_clarification"] is False
    assert out["task_frame"]["awaiting_execute_decision"] is False
    assert out["task_frame"]["clarification_questions"] == []
    assert out["task_frame"]["execute_resume_goal"] == "剪一个片头"
