from __future__ import annotations

import pytest

import app.tools  # noqa: F401 — registers tools

from app.config import get_settings
from app.sandbox.manager import get_sandbox_manager
from app.tools import registry
from app.tools.base import ToolContext


@pytest.fixture
def ctx():
    sm = get_sandbox_manager(get_settings())
    rec = sm.create(session_id="tooltest")
    return ToolContext(sandbox_id=rec.id, workdir=rec.workdir, session_id="tooltest")


async def test_shell_echo(ctx):
    tool = registry.get("shell")
    assert tool is not None
    res = await tool.invoke(ctx, {"cmd": "echo hello && pwd"})
    assert res.ok is True
    assert "hello" in res.output["stdout"]
    assert res.output["exit_code"] == 0


async def test_shell_auto_bootstraps_python_venv(ctx):
    tool = registry.get("shell")
    assert tool is not None
    res = await tool.invoke(
        ctx,
        {
            "cmd": "python - <<'PY'\nimport sys\nprint(sys.prefix)\nprint(sys.executable)\nPY",
        },
    )
    assert res.ok is True
    assert "/.venv" in res.output["stdout"]
    assert res.output["venv"].endswith("/.venv")


async def test_shell_nonzero_exit(ctx):
    tool = registry.get("shell")
    res = await tool.invoke(ctx, {"cmd": "exit 3"})
    assert res.ok is False
    assert res.output["exit_code"] == 3


async def test_shell_timeout(ctx):
    tool = registry.get("shell")
    res = await tool.invoke(ctx, {"cmd": "sleep 5", "timeout_sec": 1})
    assert res.ok is False
    assert "timeout" in (res.error or "")


async def test_fs_write_read_list_delete(ctx):
    tool = registry.get("filesystem")
    assert tool is not None

    w = await tool.invoke(ctx, {"action": "write", "path": "notes/hello.txt", "content": "hi there"})
    assert w.ok is True

    r = await tool.invoke(ctx, {"action": "read", "path": "notes/hello.txt"})
    assert r.ok is True
    assert r.output["content"] == "hi there"

    lst = await tool.invoke(ctx, {"action": "list", "path": "notes"})
    assert lst.ok is True
    assert any(e["name"] == "hello.txt" for e in lst.output["entries"])

    st = await tool.invoke(ctx, {"action": "stat", "path": "notes/hello.txt"})
    assert st.ok is True and st.output["is_file"] is True

    d = await tool.invoke(ctx, {"action": "delete", "path": "notes/hello.txt"})
    assert d.ok is True


async def test_fs_path_escape_rejected(ctx):
    tool = registry.get("filesystem")
    res = await tool.invoke(ctx, {"action": "write", "path": "../../etc/evil", "content": "x"})
    assert res.ok is False
    assert "escapes" in (res.error or "")


async def test_search_mock_when_provider_forced(ctx):
    tool = registry.get("search")
    settings = get_settings()
    old_provider = settings.search_provider
    settings.search_provider = "mock"
    res = await tool.invoke(ctx, {"query": "manus agent", "top_k": 3})
    settings.search_provider = old_provider
    assert res.ok is True
    assert len(res.output["results"]) == 3
    assert res.output["provider"] == "mock"


async def test_media_tools_registered():
    assert registry.get("wan_text2image") is not None
    assert registry.get("wan_text2video") is not None
    assert registry.get("minimax_tts") is not None


async def test_wan_text2image_requires_key(ctx):
    tool = registry.get("wan_text2image")
    assert tool is not None
    settings = get_settings()
    old = settings.dashscope_api_key
    settings.dashscope_api_key = ""
    try:
        res = await tool.invoke(ctx, {"prompt": "a cat"})
        assert res.ok is False
        assert "DASHSCOPE_API_KEY" in (res.error or "")
    finally:
        settings.dashscope_api_key = old


async def test_wan_text2video_empty_prompt(ctx):
    tool = registry.get("wan_text2video")
    assert tool is not None
    res = await tool.invoke(ctx, {"prompt": ""})
    assert res.ok is False
    assert "prompt" in (res.error or "").lower()


async def test_minimax_tts_requires_key(ctx):
    tool = registry.get("minimax_tts")
    assert tool is not None
    settings = get_settings()
    old = settings.minimax_api_key
    settings.minimax_api_key = ""
    try:
        res = await tool.invoke(ctx, {"text": "hello"})
        assert res.ok is False
        assert "MINIMAX_API_KEY" in (res.error or "")
    finally:
        settings.minimax_api_key = old


@pytest.mark.asyncio
async def test_wan_text2image_wan26_uses_image_generation_api(ctx, monkeypatch):
    """万相 2.6 T2I 应走 image-generation/generation + messages 形态。"""
    import app.tools.media_tools as mt

    tool = registry.get("wan_text2image")
    assert tool is not None
    settings = get_settings()
    old_k = settings.dashscope_api_key
    settings.dashscope_api_key = "sk-test"
    try:
        calls: list[tuple[str, dict]] = []

        async def fake_post(client, base, key, path, body):
            calls.append((path, body))
            return {"output": {"task_id": "task-w26"}}

        async def fake_poll(_client, **_kw):
            return {
                "output": {
                    "task_status": "SUCCEEDED",
                    "results": [{"url": "http://example.test/out.png"}],
                }
            }

        async def fake_download(client, url, timeout_sec):
            return b"\x89PNG\r\n\x1a\n"

        monkeypatch.setattr(mt, "_dashscope_post", fake_post)
        monkeypatch.setattr(mt, "_poll_dashscope_task", fake_poll)
        monkeypatch.setattr(mt, "_download_bytes", fake_download)

        res = await tool.invoke(
            ctx,
            {"prompt": "一只猫", "model": "wan2.6-t2i", "negative_prompt": "模糊"},
        )
        assert res.ok is True
        assert calls
        path, body = calls[0]
        assert path == "/services/aigc/image-generation/generation"
        assert body["model"] == "wan2.6-t2i"
        msgs = body["input"]["messages"]
        assert msgs[0]["role"] == "user"
        assert msgs[0]["content"] == [{"text": "一只猫"}]
        assert body["parameters"]["size"] == "1280*1280"
        assert body["parameters"]["negative_prompt"] == "模糊"
    finally:
        settings.dashscope_api_key = old_k


@pytest.mark.asyncio
async def test_wan_text2image_wan22_uses_text2image_synthesis(ctx, monkeypatch):
    """万相 2.2 系仍走 text2image/image-synthesis + input.prompt。"""
    import app.tools.media_tools as mt

    tool = registry.get("wan_text2image")
    assert tool is not None
    settings = get_settings()
    old_k = settings.dashscope_api_key
    settings.dashscope_api_key = "sk-test"
    try:
        calls: list[tuple[str, dict]] = []

        async def fake_post(client, base, key, path, body):
            calls.append((path, body))
            return {"output": {"task_id": "task-22"}}

        async def fake_poll(_client, **_kw):
            return {
                "output": {
                    "task_status": "SUCCEEDED",
                    "results": [{"url": "http://example.test/out.png"}],
                }
            }

        async def fake_download(client, url, timeout_sec):
            return b"\x89PNG\r\n\x1a\n"

        monkeypatch.setattr(mt, "_dashscope_post", fake_post)
        monkeypatch.setattr(mt, "_poll_dashscope_task", fake_poll)
        monkeypatch.setattr(mt, "_download_bytes", fake_download)

        res = await tool.invoke(ctx, {"prompt": "狗", "model": "wan2.2-t2i-flash"})
        assert res.ok is True
        path, body = calls[0]
        assert path == "/services/aigc/text2image/image-synthesis"
        assert body["input"]["prompt"] == "狗"
        assert "messages" not in body["input"]
    finally:
        settings.dashscope_api_key = old_k


@pytest.mark.asyncio
async def test_wan_text2video_happyhorse_sends_resolution_ratio_duration(ctx, monkeypatch):
    """HappyHorse 文生视频应传 resolution / ratio / duration，不传万相 size。"""
    import app.tools.media_tools as mt

    tool = registry.get("wan_text2video")
    assert tool is not None
    settings = get_settings()
    old_k = settings.dashscope_api_key
    settings.dashscope_api_key = "sk-test"
    try:
        bodies: list[dict] = []

        async def fake_post(client, base, key, path, body):
            bodies.append(body)
            return {"output": {"task_id": "task-hh"}}

        async def fake_poll(_client, **_kw):
            return {
                "output": {
                    "task_status": "SUCCEEDED",
                    "video_url": "http://example.test/a.mp4",
                }
            }

        async def fake_download(client, url, timeout_sec):
            return b"\x00\x00\x00\x18ftypmp42"

        monkeypatch.setattr(mt, "_dashscope_post", fake_post)
        monkeypatch.setattr(mt, "_poll_dashscope_task", fake_poll)
        monkeypatch.setattr(mt, "_download_bytes", fake_download)

        res = await tool.invoke(
            ctx,
            {
                "prompt": "海浪",
                "model": "happyhorse-1.0-t2v",
                "resolution": "720P",
                "ratio": "16:9",
                "duration": 8,
            },
        )
        assert res.ok is True
        assert bodies
        b0 = bodies[0]
        assert b0["model"] == "happyhorse-1.0-t2v"
        assert b0["input"] == {"prompt": "海浪"}
        assert b0["parameters"]["resolution"] == "720P"
        assert b0["parameters"]["ratio"] == "16:9"
        assert b0["parameters"]["duration"] == 8
        assert "size" not in b0["parameters"]
    finally:
        settings.dashscope_api_key = old_k

