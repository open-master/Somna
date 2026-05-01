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
async def test_reflect_finalize_marks_plan_done():
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

    assert out["next_node"] == "finalize"
    assert out["plan"]["todos"][1]["status"] == "done"


@pytest.mark.asyncio
async def test_reflect_finalize_mode2_skips_finish_all():
    """模式二 finalize 不强行标满 TODO，保留执行阶段真实进度。"""
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
                "user_message": "词云",
                "assistant_text": "已完成",
                "execution_summary": {},
                "plan": {
                    "todos": [
                        {"id": "1", "text": "步骤一", "status": "done"},
                        {"id": "2", "text": "步骤二", "status": "pending"},
                    ]
                },
            }
        )

    assert out["next_node"] == "finalize"
    assert out["plan"]["todos"][1]["status"] == "pending"


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
            }
        )

    assert out["next_node"] == "execute"
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
            }
        )

    assert out["next_node"] == "plan"
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
