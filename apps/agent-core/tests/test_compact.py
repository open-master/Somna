"""Compact helper tests."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.graph import compact as cm


class _SettingsStub:
    agent_compact_token_threshold = 20  # ~80 chars
    agent_compact_model = "agent-cheap"
    agent_default_longctx = "agent-longctx"


def _mk_completion(content: str):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


def test_estimate_tokens_approximates_char_count():
    msgs = [HumanMessage(content="a" * 40), AIMessage(content="b" * 40)]
    assert cm.estimate_tokens(msgs) == 20  # 80 chars / 4


@pytest.mark.asyncio
async def test_maybe_compact_no_op_below_threshold():
    msgs = [HumanMessage(content="short")]
    with patch.object(cm, "get_settings", return_value=_SettingsStub()):
        new_msgs, did, summary = await cm.maybe_compact(msgs, session_id=uuid4(), run_id="r1")
    assert did is False
    assert new_msgs is msgs
    assert summary is None


@pytest.mark.asyncio
async def test_maybe_compact_no_op_when_not_enough_messages():
    # Huge single message but not enough history to compress meaningfully.
    msgs = [HumanMessage(content="x" * 1000)]
    with patch.object(cm, "get_settings", return_value=_SettingsStub()):
        new_msgs, did, summary = await cm.maybe_compact(msgs, session_id=uuid4(), run_id="r1")
    assert did is False
    assert summary is None


@pytest.mark.asyncio
async def test_maybe_compact_summarises_head():
    # 10 bulky messages -> head gets compressed to a SystemMessage summary.
    msgs = []
    for i in range(10):
        msgs.append(HumanMessage(content=f"user message #{i} " * 20))
        msgs.append(AIMessage(content=f"assistant reply #{i} " * 20))

    client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=AsyncMock(return_value=_mk_completion("## 会话目标\n摘要"))
            )
        )
    )

    with (
        patch.object(cm, "get_settings", return_value=_SettingsStub()),
        patch.object(cm, "get_async_openai", return_value=client),
        patch.object(cm, "emit", AsyncMock()),
        patch.object(cm, "load_template", return_value="compact {{history}}"),
    ):
        new_msgs, did, summary = await cm.maybe_compact(msgs, session_id=uuid4(), run_id="r1")

    assert did is True
    assert summary == "## 会话目标\n摘要"
    assert isinstance(new_msgs[0], SystemMessage)
    assert "摘要" in new_msgs[0].content
    # Tail preserved
    assert len(new_msgs) == 1 + cm.KEEP_TAIL


@pytest.mark.asyncio
async def test_maybe_compact_falls_back_on_empty_summary():
    msgs = []
    for i in range(10):
        msgs.append(HumanMessage(content="x" * 100))

    client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=AsyncMock(return_value=_mk_completion("   "))
            )
        )
    )

    with (
        patch.object(cm, "get_settings", return_value=_SettingsStub()),
        patch.object(cm, "get_async_openai", return_value=client),
        patch.object(cm, "emit", AsyncMock()),
        patch.object(cm, "load_template", return_value="compact {{history}}"),
    ):
        new_msgs, did, summary = await cm.maybe_compact(msgs, session_id=uuid4(), run_id="r1")

    assert did is False
    assert new_msgs is msgs
    assert summary is None


@pytest.mark.asyncio
async def test_maybe_compact_prefers_longctx_model():
    msgs = []
    for i in range(10):
        msgs.append(HumanMessage(content=f"u{i} " * 20))
        msgs.append(AIMessage(content=f"a{i} " * 20))

    create_mock = AsyncMock(return_value=_mk_completion("摘要"))
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create_mock))
    )

    with (
        patch.object(cm, "get_settings", return_value=_SettingsStub()),
        patch.object(cm, "get_async_openai", return_value=client),
        patch.object(cm, "emit", AsyncMock()),
        patch.object(cm, "load_template", return_value="compact {{history}}"),
    ):
        await cm.maybe_compact(
            msgs,
            session_id=uuid4(),
            run_id="r1",
            compact_model="cheap-m",
            longctx_model="long-m",
        )

    assert create_mock.await_args.kwargs["model"] == "long-m"
