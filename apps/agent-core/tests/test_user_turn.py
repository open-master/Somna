from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from app.graph.user_turn import prior_conversation_text


def test_prior_conversation_text_excludes_current_turn_and_internal_messages():
    state = {
        "messages": [
            HumanMessage(content="乔布斯是谁？"),
            AIMessage(content="他是苹果公司的联合创始人。"),
            SystemMessage(content="内部执行指令"),
            ToolMessage(content="内部工具结果", tool_call_id="call_1"),
            HumanMessage(content="那他的妻子呢？"),
        ]
    }

    context = prior_conversation_text(state)

    assert "乔布斯是谁" in context
    assert "苹果公司的联合创始人" in context
    assert "那他的妻子呢" not in context
    assert "内部执行指令" not in context
    assert "内部工具结果" not in context


def test_prior_conversation_text_keeps_recent_tail_with_limit():
    state = {
        "messages": [
            HumanMessage(content="很早的问题"),
            AIMessage(content="A" * 200),
            HumanMessage(content="当前问题"),
        ]
    }

    context = prior_conversation_text(state, max_chars=80)

    assert context.startswith("…\n")
    assert len(context) <= 82
    assert "当前问题" not in context
