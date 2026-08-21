from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.graph.nodes import execute as exe
from app.graph.nodes import execute_agent_sdk as sdk


def test_sdk_retry_prompt_omits_tool_transcript():
    body = [
        HumanMessage(content="做乔布斯词云"),
        AIMessage(content="我先搜索资料"),
        ToolMessage(content="search hits " + ("x" * 200), tool_call_id="c1", name="search"),
    ]
    proof = exe._ExecutionProof(successful_tool_calls=1)
    prompt = sdk._body_chain_to_user_prompt(
        body,
        user_message="做乔布斯词云",
        retry_instruction="必须写入真实文件",
        proof=proof,
    )

    assert "必须写入真实文件" in prompt
    assert "做乔布斯词云" in prompt
    assert "search hits" not in prompt
    assert "【工具" not in prompt
    assert "已成功工具调用：1" in prompt


def test_sdk_initial_prompt_keeps_user_and_skips_tools():
    body = [
        HumanMessage(content="做乔布斯词云"),
        AIMessage(content="先列大纲"),
        ToolMessage(content="huge tool dump", tool_call_id="c1", name="search"),
    ]
    prompt = sdk._body_chain_to_user_prompt(
        body,
        user_message="做乔布斯词云",
        retry_instruction=None,
    )

    assert "【用户】" in prompt
    assert "先列大纲" in prompt
    assert "huge tool dump" not in prompt
