"""Plan node tests — LLM is mocked."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from somna_events import TodoStatus

from app.graph.nodes import plan as plan_mod


def _mk_completion(content: str):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


def test_parse_plan_direct_json():
    raw = json.dumps(
        {"reasoning": "split the task", "todos": [{"id": "1", "text": "a"}], "estimated_steps": 1}
    )
    parsed = plan_mod._parse_plan(raw)
    assert parsed and parsed["todos"][0]["text"] == "a"


def test_parse_plan_extracts_json_from_prose():
    raw = 'Here is the plan:\n```json\n{"todos":[{"id":"1","text":"hi"}]}\n```\nEnd.'
    parsed = plan_mod._parse_plan(raw)
    assert parsed and parsed["todos"][0]["text"] == "hi"


def test_parse_plan_returns_none_on_garbage():
    assert plan_mod._parse_plan("not json at all") is None
    assert plan_mod._parse_plan("") is None


def test_coerce_todos_filters_empty_and_defaults_id():
    out = plan_mod._coerce_todos(
        [
            {"id": "1", "text": "hello"},
            {"text": ""},  # empty, skipped
            {"text": "second"},  # no id -> defaulted
        ]
    )
    assert [t.text for t in out] == ["hello", "second"]
    assert out[1].id == "2"  # default = index + 1


@pytest.mark.asyncio
async def test_plan_node_happy_path_emits_event_and_state():
    sid = uuid4()
    raw = json.dumps(
        {
            "reasoning": "小步走",
            "todos": [
                {"id": "1", "text": "读取用户需求"},
                {"id": "2", "text": "执行命令"},
            ],
            "estimated_steps": 2,
        }
    )
    client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=AsyncMock(return_value=_mk_completion(raw))
            )
        )
    )

    emitted: list = []

    async def _emit(ev):
        emitted.append(ev)

    with (
        patch.object(plan_mod, "get_async_openai", return_value=client),
        patch.object(plan_mod, "emit", _emit),
        patch.object(plan_mod, "tool_manifest_cache", return_value={}),
        patch.object(plan_mod, "load_template", return_value="prompt {{user_message}}"),
    ):
        out = await plan_mod.plan_node(
            {
                "session_id": sid,
                "run_id": "r1",
                "user_message": "帮我跑 echo",
                "messages": [],
            }
        )

    assert out["plan"]["todos"][0]["text"] == "读取用户需求"
    assert len(out["messages"]) == 1  # nudge SystemMessage appended
    assert any(type(e).__name__ == "PlanUpdateEvent" for e in emitted)
    assert sum(type(e).__name__ == "StatusEvent" for e in emitted) == 2


@pytest.mark.asyncio
async def test_plan_node_injects_retrieved_memories_into_prompt():
    sid = uuid4()
    raw = json.dumps({"reasoning": "参考历史偏好", "todos": [{"id": "1", "text": "继续用 Next.js"}]})
    client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=AsyncMock(return_value=_mk_completion(raw))
            )
        )
    )
    rendered: dict[str, str] = {}

    def _render(_template: str, **kwargs):
        rendered["retrieved_memories"] = kwargs["retrieved_memories"]
        return "prompt"

    with (
        patch.object(plan_mod, "get_async_openai", return_value=client),
        patch.object(plan_mod, "emit", AsyncMock()),
        patch.object(plan_mod, "tool_manifest_cache", return_value={}),
        patch.object(plan_mod, "load_template", return_value="prompt {{retrieved_memories}}"),
        patch.object(
            plan_mod,
            "search_memories",
            AsyncMock(return_value=[SimpleNamespace(id="m1", text="用户偏好：继续使用 Next.js")]),
        ),
        patch.object(plan_mod, "render", side_effect=_render),
    ):
        out = await plan_mod.plan_node(
            {
                "session_id": sid,
                "run_id": "r1",
                "user_message": "帮我继续完善之前的网站",
                "messages": [],
            }
        )

    assert out["plan"] is not None
    assert "Next.js" in rendered["retrieved_memories"]


@pytest.mark.asyncio
async def test_plan_node_llm_failure_returns_no_plan():
    sid = uuid4()
    client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=AsyncMock(side_effect=RuntimeError("boom")))
        )
    )

    with (
        patch.object(plan_mod, "get_async_openai", return_value=client),
        patch.object(plan_mod, "emit", AsyncMock()),
        patch.object(plan_mod, "tool_manifest_cache", return_value={}),
        patch.object(plan_mod, "load_template", return_value="prompt"),
    ):
        out = await plan_mod.plan_node(
            {"session_id": sid, "run_id": "r1", "user_message": "x", "messages": []}
        )

    assert out == {"plan": None}


@pytest.mark.asyncio
async def test_plan_node_missing_template_skips():
    sid = uuid4()
    with (
        patch.object(plan_mod, "load_template", return_value=""),
    ):
        out = await plan_mod.plan_node(
            {"session_id": sid, "run_id": "r1", "user_message": "x", "messages": []}
        )
    assert out == {"plan": None}


@pytest.mark.asyncio
async def test_plan_node_respects_skip_flag():
    sid = uuid4()
    with (
        patch.object(plan_mod, "emit", AsyncMock()),
        patch.object(plan_mod, "get_async_openai", side_effect=AssertionError("should not call llm")),
    ):
        out = await plan_mod.plan_node(
            {
                "session_id": sid,
                "run_id": "r1",
                "user_message": "x",
                "messages": [],
                "skip_planner": True,
                "plan": {
                    "todos": [
                        {"id": "1", "text": "已有步骤", "status": "in_progress"},
                    ]
                },
            }
        )
    assert "plan" not in out


@pytest.mark.asyncio
async def test_advance_with_proof_completes_generic_todo_on_successful_tool_call():
    sid = uuid4()
    plan = {
        "todos": [
            {"id": "1", "text": "搜索资料", "status": "in_progress"},
            {"id": "2", "text": "整理答案", "status": "pending"},
        ]
    }
    proof = SimpleNamespace(successful_tool_calls=1, written_paths=set(), verified_paths=set())

    with patch.object(plan_mod, "emit", AsyncMock()):
        out = await plan_mod.advance_with_proof(plan, session_id=sid, run_id="r1", proof=proof)

    assert out is not None
    assert out["todos"][0]["status"] == TodoStatus.done
    assert out["todos"][1]["status"] == TodoStatus.in_progress
    assert out["todos"][0]["tool_call_count"] == 1


@pytest.mark.asyncio
async def test_advance_with_proof_requires_artifact_evidence_for_build_todo():
    sid = uuid4()
    plan = {
        "todos": [
            {"id": "1", "text": "写前端页面", "status": "in_progress"},
            {"id": "2", "text": "本地验证", "status": "pending"},
        ]
    }
    install_only = SimpleNamespace(successful_tool_calls=1, written_paths=set(), verified_paths=set())
    verified_only = SimpleNamespace(
        successful_tool_calls=1,
        written_paths=set(),
        verified_paths={"/workspace/app/existing.tsx"},
    )
    file_proof = SimpleNamespace(
        successful_tool_calls=1,
        written_paths={"/workspace/app/page.tsx"},
        verified_paths=set(),
    )

    with patch.object(plan_mod, "emit", AsyncMock()):
        mid = await plan_mod.advance_with_proof(plan, session_id=sid, run_id="r1", proof=install_only)
        verified = await plan_mod.advance_with_proof(
            mid, session_id=sid, run_id="r1", proof=verified_only
        )
        out = await plan_mod.advance_with_proof(
            verified, session_id=sid, run_id="r1", proof=file_proof
        )

    assert mid is not None
    assert mid["todos"][0]["status"] == TodoStatus.in_progress
    assert mid["todos"][1]["status"] == TodoStatus.pending
    assert verified is not None
    assert verified["todos"][0]["status"] == TodoStatus.in_progress
    assert verified["todos"][1]["status"] == TodoStatus.pending
    assert out is not None
    assert out["todos"][0]["status"] == TodoStatus.done
    assert out["todos"][1]["status"] == TodoStatus.in_progress
    assert out["todos"][0]["evidence_paths"] == ["/workspace/app/page.tsx"]
