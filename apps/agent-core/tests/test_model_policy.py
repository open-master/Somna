"""Tests for executor vs coder model selection."""

from __future__ import annotations

from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.graph import model_policy as mp


def test_first_turn_uses_executor():
    m1 = SimpleNamespace(name="search", category="net")
    by = {"search": m1}
    msgs = [HumanMessage(content="hi")]
    assert (
        mp.pick_executor_turn_model(
            working_messages=msgs,
            manifest_by_name=by,
            executor_model="exec",
            coder_model="cod",
        )
        == "exec"
    )


def test_after_shell_tool_uses_coder_when_distinct():
    msgs = [
        HumanMessage(content="run"),
        AIMessage(
            content="",
            additional_kwargs={
                "tool_calls": [{"name": "shell", "args": {"cmd": "echo"}, "id": "c1"}]
            },
        ),
        ToolMessage(content="ok", tool_call_id="c1", name="shell"),
    ]
    assert (
        mp.pick_executor_turn_model(
            working_messages=msgs,
            manifest_by_name={"shell": SimpleNamespace(name="shell", category="os")},
            executor_model="exec",
            coder_model="cod",
        )
        == "cod"
    )


def test_same_executor_and_coder_returns_executor_string():
    msgs = [
        HumanMessage(content="run"),
        AIMessage(
            content="",
            additional_kwargs={
                "tool_calls": [{"name": "shell", "args": {}, "id": "c1"}]
            },
        ),
        ToolMessage(content="ok", tool_call_id="c1", name="shell"),
    ]
    assert (
        mp.pick_executor_turn_model(
            working_messages=msgs,
            manifest_by_name={},
            executor_model="same",
            coder_model="same",
        )
        == "same"
    )
