from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from langchain_core.messages import SystemMessage

from app.graph.nodes import reflect as reflect_mod


def _mk_completion(content: str):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


@pytest.mark.asyncio
async def test_reflect_rejects_finalize_while_plan_has_pending_todos():
    sid = uuid4()
    client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=AsyncMock(return_value=_mk_completion('{"decision":"finalize","reason":"可以结束","focus":""}'))
            )
        )
    )

    with (
        patch.object(reflect_mod, "emit", AsyncMock()),
        patch.object(reflect_mod, "get_async_openai", return_value=client),
    ):
        out = await reflect_mod.reflect_node(
            {
                "session_id": sid,
                "run_id": "r1",
                "user_message": "帮我做一个网站",
                "assistant_text": "已完成网站",
                "execution_summary": {"written_paths": ["/workspace/app/page.tsx"]},
                "plan": {
                    "todos": [
                        {"id": "1", "text": "写前端页面", "status": "done"},
                        {"id": "2", "text": "自检结果", "status": "pending"},
                    ]
                },
            }
        )

    assert out["next_node"] == "execute"
    assert out["plan"]["todos"][1]["status"] == "pending"
    assert "未完成 TODO" in out["reflection"]["reason"]


@pytest.mark.asyncio
async def test_reflect_at_cap_closes_unfinished_todos_truthfully():
    """反思预算耗尽时可收口，但未完成项不能被伪装成 done。"""
    sid = uuid4()
    client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=AsyncMock(return_value=_mk_completion('{"decision":"finalize","reason":"模型结论","focus":""}'))
            )
        )
    )

    with (
        patch.object(reflect_mod, "emit", AsyncMock()),
        patch.object(reflect_mod, "get_async_openai", return_value=client),
        patch.object(reflect_mod, "load_template", return_value="tpl"),
        patch.object(reflect_mod, "render", return_value="x"),
    ):
        out = await reflect_mod.reflect_node(
            {
                "session_id": sid,
                "run_id": "r1",
                "executor_engine": "anthropic",
                "reflection_count": 2,
                "user_message": "词云",
                "assistant_text": "已完成",
                "execution_summary": {},
                "plan": {
                    "todos": [
                        {"id": "1", "text": "步骤一", "status": "in_progress"},
                        {"id": "2", "text": "步骤二", "status": "pending"},
                    ]
                },
            }
        )

    assert out["next_node"] == "finalize"
    assert out["plan"]["todos"][0]["status"] == "failed"
    assert out["plan"]["todos"][1]["status"] == "skipped"


@pytest.mark.asyncio
async def test_reflect_finalizes_when_global_tool_budget_is_exhausted():
    sid = uuid4()
    with patch.object(reflect_mod, "emit", AsyncMock()):
        out = await reflect_mod.reflect_node(
            {
                "session_id": sid,
                "run_id": "r-budget",
                "tool_turns": reflect_mod.get_settings().agent_max_turns,
                "plan": {
                    "todos": [
                        {"id": "1", "text": "继续执行", "status": "in_progress"},
                        {"id": "2", "text": "验收", "status": "pending"},
                    ]
                },
            }
        )

    assert out["next_node"] == "finalize"
    assert out["reflection"]["reason"] == "已达到本轮全局执行预算上限"
    assert out["plan"]["todos"][0]["status"] == "failed"
    assert out["plan"]["todos"][1]["status"] == "skipped"


@pytest.mark.asyncio
async def test_reflect_continue_execute_appends_guidance_message():
    sid = uuid4()
    with (
        patch.object(reflect_mod, "emit", AsyncMock()),
        patch.object(reflect_mod, "load_template", return_value=""),
    ):
        out = await reflect_mod.reflect_node(
            {
                "session_id": sid,
                "run_id": "r1",
                "user_message": "帮我做一个网站",
                "assistant_text": "网站已完成",
                "execution_summary": {"delivery_missing_reason": "任务要求交付真实产物，但没有检测到写文件/修改沙盒的证据"},
                "plan": {
                    "todos": [
                        {"id": "1", "text": "写前端页面", "status": "in_progress"},
                    ]
                },
                "messages": [],
                "tool_turns": 3,
            }
        )

    assert out["next_node"] == "execute"
    assert "tool_turns" not in out
    assert isinstance(out["messages"][-1], SystemMessage)
    assert "继续执行" in out["messages"][-1].content


@pytest.mark.asyncio
async def test_reflect_replan_on_multi_step_gap():
    sid = uuid4()
    with (
        patch.object(reflect_mod, "emit", AsyncMock()),
        patch.object(reflect_mod, "load_template", return_value=""),
    ):
        out = await reflect_mod.reflect_node(
            {
                "session_id": sid,
                "run_id": "r1",
                "user_message": "做一个多步骤任务",
                "assistant_text": "先给一个口头结论",
                "execution_summary": {"delivery_missing_reason": "存在多步计划，但模型没有实际调用工具就试图结束"},
                "plan": {
                    "todos": [
                        {"id": "1", "text": "抓数据", "status": "in_progress"},
                        {"id": "2", "text": "分析", "status": "pending"},
                    ]
                },
                "messages": [],
                "tool_turns": 3,
                "skip_planner": True,
            }
        )

    assert out["next_node"] == "plan"
    assert "tool_turns" not in out
    assert out["skip_planner"] is True
    assert isinstance(out["messages"][-1], SystemMessage)
    assert "重新规划" in out["messages"][-1].content


@pytest.mark.asyncio
async def test_reflect_low_autonomy_success_criteria_first_pass_fallback_continues():
    """首轮反思 + 低自主 + 定调含 success_criteria：无 LLM 时 fallback 多给一轮执行自检。"""
    sid = uuid4()
    with (
        patch.object(reflect_mod, "emit", AsyncMock()),
        patch.object(reflect_mod, "load_template", return_value=""),
    ):
        out = await reflect_mod.reflect_node(
            {
                "session_id": sid,
                "run_id": "r1",
                "reflection_count": 0,
                "task_frame": {"autonomy_level": "low", "success_criteria": ["验收 A", "验收 B"]},
                "user_message": "任务",
                "assistant_text": "初步结论",
                "execution_summary": {},
                "plan": {
                    "todos": [
                        {"id": "1", "text": "步骤一", "status": "done"},
                    ]
                },
                "messages": [],
            }
        )

    assert out["next_node"] == "execute"
    assert out["reflection"]["decision"] == "continue_execute"
    assert "success_criteria" in (out["reflection"]["reason"] or "") or "自检" in (out["reflection"]["reason"] or "")


@pytest.mark.asyncio
async def test_reflect_high_autonomy_coerces_continue_execute_to_finalize():
    sid = uuid4()
    client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=AsyncMock(
                    return_value=_mk_completion(
                        '{"decision":"continue_execute","reason":"再检查一遍","focus":"无"}'
                    )
                )
            )
        )
    )

    with (
        patch.object(reflect_mod, "emit", AsyncMock()),
        patch.object(reflect_mod, "get_async_openai", return_value=client),
    ):
        out = await reflect_mod.reflect_node(
            {
                "session_id": sid,
                "run_id": "r1",
                "task_frame": {"autonomy_level": "high", "risk_level": "low"},
                "user_message": "任务",
                "assistant_text": "已完成",
                "execution_summary": {},
                "plan": {
                    "todos": [
                        {"id": "1", "text": "步骤一", "status": "done"},
                    ]
                },
            }
        )

    assert out["next_node"] == "finalize"
    assert out["reflection"]["decision"] == "finalize"


@pytest.mark.asyncio
async def test_reflect_high_risk_caps_autonomy_no_coerce_from_model_continue():
    """high risk 将声明 high 压到 medium：不因「高自主收口」把 continue 改成 finalize。"""
    sid = uuid4()
    client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=AsyncMock(
                    return_value=_mk_completion(
                        '{"decision":"continue_execute","reason":"继续","focus":"补证据"}'
                    )
                )
            )
        )
    )

    with (
        patch.object(reflect_mod, "emit", AsyncMock()),
        patch.object(reflect_mod, "get_async_openai", return_value=client),
    ):
        out = await reflect_mod.reflect_node(
            {
                "session_id": sid,
                "run_id": "r1",
                "task_frame": {"autonomy_level": "high", "risk_level": "high"},
                "user_message": "任务",
                "assistant_text": "已完成",
                "execution_summary": {},
                "plan": {
                    "todos": [
                        {"id": "1", "text": "步骤一", "status": "done"},
                    ]
                },
            }
        )

    assert out["next_node"] == "execute"
    assert out["reflection"]["decision"] == "continue_execute"


def test_has_artifact_evidence_ignores_verified_paths():
    assert reflect_mod._has_artifact_evidence({"verified_paths": ["/workspace/old.png"]}) is False
    assert reflect_mod._has_artifact_evidence({"written_paths": ["/workspace/new.png"]}) is True
    assert reflect_mod._has_artifact_evidence({}) is False


@pytest.mark.asyncio
async def test_reflect_blocks_skill_finalize_when_only_verified_paths_exist():
    sid = uuid4()
    client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=AsyncMock(return_value=_mk_completion('{"decision":"finalize","reason":"文件已在","focus":""}'))
            )
        )
    )

    with (
        patch.object(reflect_mod, "emit", AsyncMock()),
        patch.object(reflect_mod, "get_async_openai", return_value=client),
        patch.object(reflect_mod, "load_template", return_value="tpl"),
        patch.object(reflect_mod, "render", return_value="x"),
    ):
        out = await reflect_mod.reflect_node(
            {
                "session_id": sid,
                "run_id": "r1",
                "user_message": "做一个网站",
                "assistant_text": "已经有页面了",
                "selected_skills": [{"name": "frontend-design", "load_files": []}],
                "task_frame": {"deliverable_type": "website"},
                "execution_summary": {"verified_paths": ["/workspace/app/page.tsx"]},
                "plan": {"todos": [{"id": "1", "text": "写页面", "status": "done"}]},
            }
        )

    assert out["next_node"] == "execute"
    assert "产物证据" in out["reflection"]["reason"]


@pytest.mark.asyncio
async def test_reflect_unrecovered_failures_blocks_finalize():
    sid = uuid4()
    client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=AsyncMock(return_value=_mk_completion('{"decision":"finalize","reason":"可以结束","focus":""}'))
            )
        )
    )

    with (
        patch.object(reflect_mod, "emit", AsyncMock()),
        patch.object(reflect_mod, "get_async_openai", return_value=client),
        patch.object(reflect_mod, "load_template", return_value="tpl"),
        patch.object(reflect_mod, "render", return_value="x"),
    ):
        out = await reflect_mod.reflect_node(
            {
                "session_id": sid,
                "run_id": "r1",
                "user_message": "跑一下脚本",
                "assistant_text": "已经完成",
                "execution_summary": {
                    "written_paths": ["/workspace/out.txt"],
                    "unrecovered_failures": 1,
                    "failed_tool_calls": 1,
                    "recovered_failures": 0,
                    "failure_notes": ["exit_code=1"],
                },
                "plan": {"todos": [{"id": "1", "text": "执行脚本", "status": "done"}]},
            }
        )

    assert out["next_node"] == "execute"
    assert "未恢复" in out["reflection"]["reason"]


@pytest.mark.asyncio
async def test_reflect_high_autonomy_does_not_coerce_unrecovered_failures():
    sid = uuid4()
    client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=AsyncMock(
                    return_value=_mk_completion(
                        '{"decision":"continue_execute","reason":"再检查一遍","focus":"无"}'
                    )
                )
            )
        )
    )

    with (
        patch.object(reflect_mod, "emit", AsyncMock()),
        patch.object(reflect_mod, "get_async_openai", return_value=client),
    ):
        out = await reflect_mod.reflect_node(
            {
                "session_id": sid,
                "run_id": "r1",
                "task_frame": {"autonomy_level": "high", "risk_level": "low"},
                "user_message": "任务",
                "assistant_text": "已完成",
                "execution_summary": {
                    "unrecovered_failures": 1,
                    "failure_notes": ["timeout"],
                },
                "plan": {
                    "todos": [
                        {"id": "1", "text": "步骤一", "status": "done"},
                    ]
                },
            }
        )

    assert out["next_node"] == "execute"
    assert out["reflection"]["decision"] == "continue_execute"


@pytest.mark.asyncio
async def test_reflect_execute_exception_with_progress_continues():
    sid = uuid4()
    client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=AsyncMock(return_value=_mk_completion('{"decision":"finalize","reason":"可以结束","focus":""}'))
            )
        )
    )

    with (
        patch.object(reflect_mod, "emit", AsyncMock()),
        patch.object(reflect_mod, "get_async_openai", return_value=client),
        patch.object(reflect_mod, "load_template", return_value="tpl"),
        patch.object(reflect_mod, "render", return_value="x"),
    ):
        out = await reflect_mod.reflect_node(
            {
                "session_id": sid,
                "run_id": "r1",
                "user_message": "跑脚本",
                "assistant_text": "中断前已写出文件",
                "execution_summary": {
                    "written_paths": ["/workspace/out.txt"],
                    "successful_tool_calls": 1,
                    "execute_exception": "hub down",
                },
                "plan": {"todos": [{"id": "1", "text": "执行脚本", "status": "done"}]},
            }
        )

    assert out["next_node"] == "execute"
    assert "异常" in out["reflection"]["reason"]
