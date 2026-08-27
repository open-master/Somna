"""Plan node tests — LLM is mocked."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from somna_events import TodoStatus

from app.graph.nodes import plan as plan_mod
from app.tools.client import ToolManifest


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
    assert plan_mod._parse_plan('[{"text":"not an object root"}]') is None


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
    assert out[1].depends_on == ["1"]


def test_coerce_todos_preserves_contract_and_forces_sequential_dependency():
    out = plan_mod._coerce_todos(
        [
            {
                "id": "a",
                "text": "生成脚本文件",
                "tool_hint": "file",
                "acceptance_criteria": ["脚本文件存在"],
                "expected_outputs": ["storyboard.md"],
            },
            {"id": "b", "text": "生成音频", "depends_on": []},
        ]
    )
    assert out[0].tool_hint == "filesystem"
    assert out[0].acceptance_criteria == ["脚本文件存在"]
    assert out[0].expected_outputs == ["storyboard.md"]
    assert out[1].depends_on == ["a"]


def test_coerce_todos_normalizes_aliases_against_executable_catalog():
    manifests = [SimpleNamespace(name="search"), SimpleNamespace(name="filesystem")]
    out = plan_mod._coerce_todos(
        [
            {
                "id": "1",
                "text": "获取维基百科资料并保存",
                "tool_hint": "browser|file|not_a_real_tool",
            }
        ],
        manifests=manifests,
    )
    assert out[0].tool_hint == "search|filesystem"


def test_coerce_todos_does_not_trust_planner_completion_status():
    out = plan_mod._coerce_todos(
        [{"id": "1", "text": "尚未执行的步骤", "status": "done"}],
        preserve_status=False,
    )
    assert out[0].status == TodoStatus.pending


def test_previous_plan_evidence_is_scoped_to_current_run():
    previous = {"run_id": "old-run", "todos": [{"id": "1", "text": "相同步骤", "status": "done"}]}
    assert plan_mod._previous_plan_for_current_run({"run_id": "new-run", "plan": previous}) is None
    assert plan_mod._previous_plan_for_current_run({"run_id": "old-run", "plan": previous}) is previous


def test_fallback_plan_from_task_frame_uses_success_criteria():
    plan = plan_mod._fallback_plan_from_task_frame(
        user_message="做网站",
        task_frame={
            "deliverable_type": "website",
            "success_criteria": ["首页可打开", "有图表"],
        },
    )
    texts = [t["text"] for t in plan["todos"]]
    assert texts[0] == "执行任务：做网站"
    assert plan["todos"][0]["acceptance_criteria"] == ["首页可打开", "有图表"]
    assert plan["fallback_from_task_frame"] is True
    assert any("交付" in t or "文件" in t for t in texts)


def test_fallback_plan_from_task_frame_without_criteria():
    plan = plan_mod._fallback_plan_from_task_frame(
        user_message="解释一下量子纠缠",
        task_frame={"deliverable_type": "chat_answer"},
    )
    texts = [t["text"] for t in plan["todos"]]
    assert len(texts) >= 2
    assert any("量子纠缠" in t or "定调" in t for t in texts)


def test_todo_intent_distinguishes_intro_text_from_video_generation():
    assert plan_mod._todo_intent("获取乔布斯的维基百科生平介绍文本") == "text_content"
    assert plan_mod._todo_intent("生成 5 段动态视频") == "video_clips"


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
        chat=SimpleNamespace(completions=SimpleNamespace(create=AsyncMock(return_value=_mk_completion(raw))))
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
        chat=SimpleNamespace(completions=SimpleNamespace(create=AsyncMock(return_value=_mk_completion(raw))))
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
async def test_plan_node_uses_same_task_scoped_catalog_as_executor():
    sid = uuid4()
    raw = json.dumps(
        {
            "reasoning": "先检索",
            "todos": [
                {
                    "id": "1",
                    "text": "获取乔布斯的维基百科生平介绍文本",
                    "tool_hint": "browser",
                }
            ],
        }
    )
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=AsyncMock(return_value=_mk_completion(raw))))
    )
    rendered: dict[str, str] = {}

    def _render(_template: str, **kwargs):
        rendered["tools_list"] = kwargs["tools_list"]
        return "prompt"

    manifests = {
        "search": ToolManifest(
            name="search", description="", input_schema={"type": "object"}, category="net"
        ),
        "wan_i2v": ToolManifest(
            name="wan_i2v", description="", input_schema={"type": "object"}, category="media"
        ),
    }
    with (
        patch.object(plan_mod, "get_async_openai", return_value=client),
        patch.object(plan_mod, "emit", AsyncMock()),
        patch.object(plan_mod, "tool_manifest_cache", return_value=manifests),
        patch.object(plan_mod, "load_template", return_value="prompt {{tools_list}}"),
        patch.object(plan_mod, "render", side_effect=_render),
    ):
        out = await plan_mod.plan_node(
            {
                "session_id": sid,
                "run_id": "r1",
                "user_message": "获取维基百科资料",
                "messages": [],
                "task_frame": {"allowed_action_scope": ["search"]},
            }
        )

    assert rendered["tools_list"] == "search"
    assert out["plan"]["todos"][0]["tool_hint"] == "search"


@pytest.mark.asyncio
async def test_plan_node_llm_failure_falls_back_to_task_frame_todos():
    sid = uuid4()
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=AsyncMock(side_effect=RuntimeError("boom"))))
    )

    with (
        patch.object(plan_mod, "get_async_openai", return_value=client),
        patch.object(plan_mod, "emit", AsyncMock()),
        patch.object(plan_mod, "persist_plan_pointer", AsyncMock(return_value=None)),
        patch.object(plan_mod, "tool_manifest_cache", return_value={}),
        patch.object(plan_mod, "load_template", return_value="prompt"),
    ):
        out = await plan_mod.plan_node(
            {
                "session_id": sid,
                "run_id": "r1",
                "user_message": "做一个房价分析网站",
                "messages": [],
                "task_frame": {
                    "deliverable_type": "website",
                    "success_criteria": ["写出可打开的首页", "图表能显示房价"],
                },
            }
        )

    assert out["plan"] is not None
    assert out["plan"]["fallback_from_task_frame"] is True
    criteria = out["plan"]["todos"][0]["acceptance_criteria"]
    assert "写出可打开的首页" in criteria
    assert "图表能显示房价" in criteria


@pytest.mark.asyncio
async def test_plan_node_missing_template_falls_back_to_task_frame():
    sid = uuid4()
    with (
        patch.object(plan_mod, "load_template", return_value=""),
        patch.object(plan_mod, "emit", AsyncMock()),
        patch.object(plan_mod, "persist_plan_pointer", AsyncMock(return_value=None)),
        patch.object(plan_mod, "tool_manifest_cache", return_value={}),
    ):
        out = await plan_mod.plan_node(
            {"session_id": sid, "run_id": "r1", "user_message": "x", "messages": []}
        )
    assert out["plan"] is not None
    assert out["plan"]["fallback_from_task_frame"] is True
    assert len(out["plan"]["todos"]) >= 2


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
    proof = SimpleNamespace(
        successful_tool_calls=1,
        written_paths=set(),
        verified_paths=set(),
        tool_names=["search"],
    )

    with patch.object(plan_mod, "emit", AsyncMock()):
        out = await plan_mod.advance_with_proof(plan, session_id=sid, run_id="r1", proof=proof)

    assert out is not None
    assert out["todos"][0]["status"] == TodoStatus.done
    assert out["todos"][1]["status"] == TodoStatus.in_progress
    assert out["todos"][0]["tool_call_count"] == 1


@pytest.mark.asyncio
async def test_advance_with_proof_shell_env_check_does_not_complete_todo():
    sid = uuid4()
    plan = {
        "todos": [
            {"id": "1", "text": "调研乔布斯和盖茨的成就与代表产品", "status": "in_progress"},
            {"id": "2", "text": "写一个约 20 秒的英文纪录片旁白脚本", "status": "pending"},
        ]
    }
    proof = SimpleNamespace(
        successful_tool_calls=1,
        written_paths=set(),
        verified_paths=set(),
        tool_names=["shell"],
    )

    with patch.object(plan_mod, "emit", AsyncMock()):
        out = await plan_mod.advance_with_proof(plan, session_id=sid, run_id="r1", proof=proof)

    assert out is not None
    assert out["todos"][0]["status"] == TodoStatus.in_progress
    assert out["todos"][1]["status"] == TodoStatus.pending
    assert out["todos"][0].get("tool_call_count", 0) == 0


@pytest.mark.asyncio
async def test_advance_with_proof_requires_artifact_evidence_for_build_todo():
    sid = uuid4()
    plan = {
        "todos": [
            {"id": "1", "text": "写前端页面", "status": "in_progress"},
            {"id": "2", "text": "本地验证", "status": "pending"},
        ]
    }
    install_only = SimpleNamespace(
        successful_tool_calls=1,
        written_paths=set(),
        verified_paths=set(),
        tool_names=["shell"],
    )
    verified_only = SimpleNamespace(
        successful_tool_calls=1,
        written_paths=set(),
        verified_paths={"/workspace/app/existing.tsx"},
        tool_names=["filesystem"],
    )
    file_proof = SimpleNamespace(
        successful_tool_calls=1,
        written_paths={"/workspace/app/page.tsx"},
        verified_paths=set(),
        tool_names=["filesystem"],
    )

    with patch.object(plan_mod, "emit", AsyncMock()):
        mid = await plan_mod.advance_with_proof(plan, session_id=sid, run_id="r1", proof=install_only)
        verified = await plan_mod.advance_with_proof(mid, session_id=sid, run_id="r1", proof=verified_only)
        out = await plan_mod.advance_with_proof(verified, session_id=sid, run_id="r1", proof=file_proof)

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


@pytest.mark.asyncio
async def test_advance_with_proof_requires_declared_output_filename():
    sid = uuid4()
    plan = {
        "todos": [
            {
                "id": "1",
                "text": "生成脚本文件",
                "status": "in_progress",
                "expected_outputs": ["storyboard.md"],
            }
        ]
    }
    wrong = SimpleNamespace(
        successful_tool_calls=1,
        written_paths={"/workspace/notes.md"},
        verified_paths=set(),
        tool_names=["filesystem"],
    )
    right = SimpleNamespace(
        successful_tool_calls=1,
        written_paths={"/workspace/storyboard.md"},
        verified_paths=set(),
        tool_names=["filesystem"],
    )

    with patch.object(plan_mod, "emit", AsyncMock()):
        mid = await plan_mod.advance_with_proof(plan, session_id=sid, run_id="r1", proof=wrong)
        out = await plan_mod.advance_with_proof(mid, session_id=sid, run_id="r1", proof=right)

    assert mid is not None
    assert mid["todos"][0]["status"] == TodoStatus.in_progress
    assert out is not None
    assert out["todos"][0]["status"] == TodoStatus.done


@pytest.mark.asyncio
async def test_advance_with_proof_one_clip_does_not_complete_multi_video_todo():
    sid = uuid4()
    plan = {
        "todos": [
            {"id": "4", "text": "按分镜生成 4-5 条横屏 16:9 原创视频片段", "status": "in_progress"},
            {"id": "5", "text": "将视频片段合成为约 20 秒连续视频，并叠加音频、对齐", "status": "pending"},
            {"id": "6", "text": "验证最终视频时长、比例、音画同步，必要时重导出", "status": "pending"},
        ]
    }
    clip = SimpleNamespace(
        successful_tool_calls=1,
        written_paths={"artifacts/wan_t2v_clip1_abcd1234.mp4"},
        verified_paths=set(),
        tool_names=["wan_t2v"],
    )

    with patch.object(plan_mod, "emit", AsyncMock()):
        out = await plan_mod.advance_with_proof(plan, session_id=sid, run_id="r1", proof=clip)

    assert out is not None
    assert out["todos"][0]["status"] == TodoStatus.in_progress
    assert out["todos"][1]["status"] == TodoStatus.pending
    assert out["todos"][2]["status"] == TodoStatus.pending
    assert out["todos"][0]["evidence_paths"] == ["artifacts/wan_t2v_clip1_abcd1234.mp4"]


@pytest.mark.asyncio
async def test_advance_with_proof_image_generation_needs_requested_count():
    sid = uuid4()
    plan = {
        "todos": [
            {"id": "1", "text": "生成 2 张配图", "status": "in_progress"},
            {"id": "2", "text": "制作页面", "status": "pending"},
        ]
    }
    first = SimpleNamespace(
        successful_tool_calls=1,
        written_paths={"artifacts/wan_t2i_first.png"},
        verified_paths=set(),
        tool_names=["wan_text2image"],
    )
    second = SimpleNamespace(
        successful_tool_calls=1,
        written_paths={"artifacts/wan_t2i_second.png"},
        verified_paths=set(),
        tool_names=["wan_text2image"],
    )

    with patch.object(plan_mod, "emit", AsyncMock()):
        mid = await plan_mod.advance_with_proof(plan, session_id=sid, run_id="r1", proof=first)
        out = await plan_mod.advance_with_proof(mid, session_id=sid, run_id="r1", proof=second)

    assert mid is not None
    assert mid["todos"][0]["status"] == TodoStatus.in_progress
    assert out is not None
    assert out["todos"][0]["status"] == TodoStatus.done
    assert out["todos"][1]["status"] == TodoStatus.in_progress


@pytest.mark.asyncio
async def test_advance_with_proof_fourth_clip_starts_mux_without_completing_it():
    sid = uuid4()
    plan = {
        "todos": [
            {
                "id": "4",
                "text": "按分镜生成 4-5 条横屏 16:9 原创视频片段",
                "status": "in_progress",
                "evidence_paths": [
                    "artifacts/wan_t2v_a.mp4",
                    "artifacts/wan_t2v_b.mp4",
                    "artifacts/wan_t2v_c.mp4",
                ],
            },
            {"id": "5", "text": "将视频片段合成为约 20 秒连续视频，并叠加音频、对齐", "status": "pending"},
            {"id": "6", "text": "验证最终视频时长、比例、音画同步，必要时重导出", "status": "pending"},
        ]
    }
    fourth = SimpleNamespace(
        successful_tool_calls=1,
        written_paths={"artifacts/wan_t2v_d.mp4"},
        verified_paths=set(),
        tool_names=["wan_t2v"],
    )

    with patch.object(plan_mod, "emit", AsyncMock()):
        out = await plan_mod.advance_with_proof(plan, session_id=sid, run_id="r1", proof=fourth)

    assert out is not None
    assert out["todos"][0]["status"] == TodoStatus.done
    assert out["todos"][1]["status"] == TodoStatus.in_progress
    assert out["todos"][2]["status"] == TodoStatus.pending


@pytest.mark.asyncio
async def test_advance_with_proof_wan_t2v_does_not_complete_mux_or_verify():
    sid = uuid4()
    plan = {
        "todos": [
            {
                "id": "5",
                "text": "将视频片段合成为约 20 秒连续视频，并叠加音频、对齐",
                "status": "in_progress",
            },
            {"id": "6", "text": "验证最终视频时长、比例、音画同步，必要时重导出", "status": "pending"},
        ]
    }
    extra_clip = SimpleNamespace(
        successful_tool_calls=1,
        written_paths={"artifacts/wan_t2v_extra.mp4"},
        verified_paths=set(),
        tool_names=["wan_t2v"],
    )

    with patch.object(plan_mod, "emit", AsyncMock()):
        out = await plan_mod.advance_with_proof(plan, session_id=sid, run_id="r1", proof=extra_clip)

    assert out is not None
    assert out["todos"][0]["status"] == TodoStatus.in_progress
    assert out["todos"][1]["status"] == TodoStatus.pending


@pytest.mark.asyncio
async def test_advance_with_proof_ffmpeg_output_completes_mux_not_verify():
    sid = uuid4()
    plan = {
        "todos": [
            {
                "id": "5",
                "text": "将视频片段合成为约 20 秒连续视频，并叠加音频、对齐",
                "status": "in_progress",
            },
            {"id": "6", "text": "验证最终视频时长、比例、音画同步，必要时重导出", "status": "pending"},
        ]
    }
    muxed = SimpleNamespace(
        successful_tool_calls=1,
        written_paths={"artifacts/final_documentary.mp4"},
        verified_paths=set(),
        tool_names=["shell"],
    )

    with patch.object(plan_mod, "emit", AsyncMock()):
        out = await plan_mod.advance_with_proof(plan, session_id=sid, run_id="r1", proof=muxed)

    assert out is not None
    assert out["todos"][0]["status"] == TodoStatus.done
    assert out["todos"][1]["status"] == TodoStatus.in_progress
    assert out["todos"][0]["evidence_paths"] == ["artifacts/final_documentary.mp4"]


@pytest.mark.asyncio
async def test_advance_with_proof_does_not_use_tts_to_retroactively_finish_script():
    sid = uuid4()
    plan = {
        "todos": [
            {
                "id": "2",
                "text": "写一个约 20 秒的英文纪录片旁白脚本，并规划 4-5 个分镜与时间轴",
                "status": "in_progress",
            },
            {"id": "3", "text": "根据脚本生成英文纪录片风格旁白音频", "status": "pending"},
            {"id": "4", "text": "按分镜生成 4-5 条横屏 16:9 原创视频片段", "status": "pending"},
        ]
    }
    tts = SimpleNamespace(
        successful_tool_calls=1,
        written_paths={"artifacts/minimax_tts_intro_ab12cd34.mp3"},
        verified_paths=set(),
        tool_names=["minimax_tts"],
    )

    with patch.object(plan_mod, "emit", AsyncMock()):
        out = await plan_mod.advance_with_proof(plan, session_id=sid, run_id="r1", proof=tts)

    assert out is not None
    assert out["todos"][0]["status"] == TodoStatus.in_progress
    assert out["todos"][1]["status"] == TodoStatus.pending
    assert out["todos"][2]["status"] == TodoStatus.pending
    assert out["todos"][0].get("evidence_paths") in (None, [])


@pytest.mark.asyncio
async def test_response_text_completes_only_current_text_content_step():
    sid = uuid4()
    plan = {
        "todos": [
            {"id": "1", "text": "撰写旁白脚本与分镜时间轴", "status": "in_progress"},
            {"id": "2", "text": "生成旁白音频", "status": "pending"},
        ]
    }
    response = "第一镜：清晨的城市逐渐苏醒。旁白介绍故事背景；第二镜切换到主人公，时间轴推进到十秒。"
    with patch.object(plan_mod, "emit", AsyncMock()):
        out = await plan_mod.advance_with_response_text(
            plan,
            session_id=sid,
            run_id="r1",
            response_text=response,
        )
    assert out["todos"][0]["status"] == TodoStatus.done
    assert out["todos"][1]["status"] == TodoStatus.in_progress
    assert "当前回复" in out["todos"][0]["completion_reason"]


@pytest.mark.asyncio
async def test_response_text_completes_wikipedia_intro_text_step():
    sid = uuid4()
    plan = {
        "todos": [
            {
                "id": "1",
                "text": "获取乔布斯的维基百科生平介绍文本",
                "status": "in_progress",
                "tool_hint": "search",
            },
            {"id": "2", "text": "制作交互式网页", "status": "pending"},
        ]
    }
    response = "史蒂夫·乔布斯是苹果公司联合创始人，参与推动了个人电脑、数字音乐与智能手机的发展。"
    with patch.object(plan_mod, "emit", AsyncMock()):
        out = await plan_mod.advance_with_response_text(
            plan,
            session_id=sid,
            run_id="r1",
            response_text=response,
        )
    assert out["todos"][0]["status"] == TodoStatus.done
    assert out["todos"][1]["status"] == TodoStatus.in_progress


@pytest.mark.asyncio
async def test_response_text_does_not_accept_a_promise_as_completion():
    sid = uuid4()
    plan = {"todos": [{"id": "1", "text": "整理最终答案", "status": "in_progress"}]}
    with patch.object(plan_mod, "emit", AsyncMock()):
        out = await plan_mod.advance_with_response_text(
            plan,
            session_id=sid,
            run_id="r1",
            response_text="我将在接下来整理并生成一份完整答案，请稍候等待处理完成。",
        )
    assert out["todos"][0]["status"] == TodoStatus.in_progress


@pytest.mark.asyncio
async def test_failed_step_is_retried_and_does_not_start_dependent_step():
    sid = uuid4()
    plan = {
        "todos": [
            {"id": "1", "text": "生成脚本文件", "status": "failed", "depends_on": []},
            {"id": "2", "text": "生成音频", "status": "pending", "depends_on": ["1"]},
        ]
    }
    with patch.object(plan_mod, "emit", AsyncMock()):
        out = await plan_mod.mark_progress(
            plan,
            session_id=sid,
            run_id="r1",
            start_next=True,
            retry_failed=True,
        )
    assert out["todos"][0]["status"] == TodoStatus.in_progress
    assert out["todos"][1]["status"] == TodoStatus.pending
    assert out["todos"][0]["attempts"] == 1


@pytest.mark.asyncio
async def test_close_unfinished_records_failed_and_skipped_reasons():
    sid = uuid4()
    plan = {
        "todos": [
            {"id": "1", "text": "当前步骤", "status": "in_progress"},
            {"id": "2", "text": "后续步骤", "status": "pending"},
        ]
    }
    with patch.object(plan_mod, "emit", AsyncMock()):
        out = await plan_mod.mark_progress(
            plan,
            session_id=sid,
            run_id="r1",
            close_unfinished=True,
            failure_reason="预算已用尽",
        )
    assert out["todos"][0]["status"] == TodoStatus.failed
    assert out["todos"][1]["status"] == TodoStatus.skipped
    assert out["todos"][0]["failure_reason"] == "预算已用尽"
    assert out["todos"][1]["failure_reason"] == "预算已用尽"


def test_replan_carries_exact_completed_todo_evidence():
    previous = {
        "todos": [
            {
                "id": "old-1",
                "text": "生成脚本文件",
                "status": "done",
                "evidence_paths": ["artifacts/storyboard.md"],
                "completion_reason": "文件已写入",
            }
        ]
    }
    todos = plan_mod._coerce_todos([{"id": "new-1", "text": "生成脚本文件"}])
    reconciled = plan_mod._reconcile_completed_todos(todos, previous)
    assert reconciled[0].status == TodoStatus.done
    assert reconciled[0].evidence_paths == ["artifacts/storyboard.md"]
