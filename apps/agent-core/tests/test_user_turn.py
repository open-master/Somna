from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from app.graph.user_turn import executor_messages_for_current_turn, prior_conversation_text


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


def test_executor_messages_scope_tool_traffic_to_current_turn():
    previous_tool_call = AIMessage(
        content="",
        tool_calls=[{"name": "shell", "args": {"cmd": "pwd"}, "id": "old_call"}],
    )
    current_tool_call = AIMessage(
        content="",
        tool_calls=[{"name": "filesystem", "args": {"action": "list"}, "id": "new_call"}],
    )
    state = {
        "messages": [
            HumanMessage(content="上一轮任务"),
            previous_tool_call,
            ToolMessage(content="旧工具结果", tool_call_id="old_call"),
            AIMessage(content="上一轮最终回答"),
            SystemMessage(content="旧内部提示"),
            HumanMessage(content="当前任务"),
            SystemMessage(content="当前 TODO"),
            current_tool_call,
            ToolMessage(content="当前工具结果", tool_call_id="new_call"),
        ]
    }

    messages = executor_messages_for_current_turn(state)

    assert [m.content for m in messages if isinstance(m, HumanMessage)] == [
        "上一轮任务",
        "当前任务",
    ]
    assert any(isinstance(m, AIMessage) and m.content == "上一轮最终回答" for m in messages)
    assert previous_tool_call not in messages
    assert not any(isinstance(m, ToolMessage) and m.content == "旧工具结果" for m in messages)
    assert any(isinstance(m, SystemMessage) and m.content == "当前 TODO" for m in messages)
    assert current_tool_call in messages
    assert any(isinstance(m, ToolMessage) and m.content == "当前工具结果" for m in messages)
