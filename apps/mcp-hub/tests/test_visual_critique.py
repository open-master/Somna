from __future__ import annotations

import base64
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.tools  # noqa: F401 — registers tools
from app.config import get_settings
from app.sandbox.manager import get_sandbox_manager
from app.tools import registry
from app.tools.base import ToolContext

_MIN_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


@pytest.fixture
def ctx_visual():
    sm = get_sandbox_manager(get_settings())
    rec = sm.create(session_id="vc_tooltest")
    Path(rec.workdir, "shot.png").write_bytes(_MIN_PNG)
    return ToolContext(sandbox_id=rec.id, workdir=rec.workdir, session_id="vc_tooltest")


def _fake_httpx_client_factory(content: str):
    resp = MagicMock()
    resp.status_code = 200
    resp.json = MagicMock(
        return_value={"choices": [{"message": {"content": content}}]},
    )

    client = MagicMock()
    client.post = AsyncMock(return_value=resp)

    class _CM:
        async def __aenter__(self):
            return client

        async def __aexit__(self, *a):
            return None

    def _factory(**kwargs):
        return _CM()

    return _factory


@pytest.mark.asyncio
async def test_visual_critique_requires_api_key(ctx_visual, monkeypatch):
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    get_settings.cache_clear()
    tool = registry.get("visual_critique")
    res = await tool.invoke(ctx_visual, {"path": "shot.png"})
    assert res.ok is False
    assert "DASHSCOPE_API_KEY" in (res.error or "")


@pytest.mark.asyncio
async def test_visual_critique_rejects_non_image(ctx_visual, monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-test")
    get_settings.cache_clear()
    Path(ctx_visual.workdir, "note.txt").write_text("hi", encoding="utf-8")
    tool = registry.get("visual_critique")
    res = await tool.invoke(ctx_visual, {"path": "note.txt"})
    assert res.ok is False
    assert "unsupported" in (res.error or "").lower()


@pytest.mark.asyncio
async def test_visual_critique_happy_path_mocked(ctx_visual, monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-test")
    get_settings.cache_clear()
    payload = (
        '{"verdict":"pass","summary":"简洁清晰","strengths":["留白得当"],'
        '"issues":[],"suggestions":[]}'
    )
    monkeypatch.setattr(
        "app.tools.visual_critique.httpx.AsyncClient",
        _fake_httpx_client_factory(payload),
    )
    tool = registry.get("visual_critique")
    res = await tool.invoke(ctx_visual, {"path": "shot.png", "context": "测试页"})
    assert res.ok is True
    assert res.output["critique"]["verdict"] == "pass"
    assert res.output["path"] == "shot.png"
    assert res.duration_ms is not None
