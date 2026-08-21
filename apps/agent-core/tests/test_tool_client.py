from __future__ import annotations

import httpx
import pytest
import respx

from app.tools.client import McpHubClient, ToolResult


@pytest.mark.asyncio
async def test_list_tools_ok():
    async with respx.mock(base_url="http://mcphub") as mock:
        route = mock.get("/v1/tools").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {
                        "name": "shell",
                        "description": "execute shell",
                        "input_schema": {"type": "object", "properties": {}},
                    }
                ],
            )
        )
        client = McpHubClient("http://mcphub", internal_token="hub-secret")
        tools = await client.list_tools()
        await client.close()
    assert [t.name for t in tools] == ["shell"]
    assert route.calls[0].request.headers["authorization"] == "Bearer hub-secret"


@pytest.mark.asyncio
async def test_invoke_401_returns_error_result():
    async with respx.mock(base_url="http://mcphub") as mock:
        mock.post("/v1/tools/shell/invoke").mock(
            return_value=httpx.Response(401, text="unauthorized")
        )
        client = McpHubClient("http://mcphub", internal_token="hub-secret")
        res = await client.invoke("shell", sandbox_id="s", args={"cmd": "echo hi"})
        await client.close()
    assert res.ok is False
    assert "unauthorized" in (res.error or "")


@pytest.mark.asyncio
async def test_invoke_maps_result():
    async with respx.mock(base_url="http://mcphub") as mock:
        mock.post("/v1/tools/shell/invoke").mock(
            return_value=httpx.Response(
                200,
                json={
                    "ok": True,
                    "preview": "hi",
                    "output": {"exit_code": 0},
                    "duration_ms": 7,
                },
            )
        )
        client = McpHubClient("http://mcphub")
        res = await client.invoke("shell", sandbox_id="s", args={"cmd": "echo hi"})
        await client.close()
    assert isinstance(res, ToolResult)
    assert res.ok is True
    assert res.preview == "hi"
    assert res.duration_ms == 7


@pytest.mark.asyncio
async def test_invoke_404_returns_error_result():
    async with respx.mock(base_url="http://mcphub") as mock:
        mock.post("/v1/tools/nope/invoke").mock(return_value=httpx.Response(404, text="not found"))
        client = McpHubClient("http://mcphub")
        res = await client.invoke("nope", sandbox_id="s", args={})
        await client.close()
    assert res.ok is False
    assert "not registered" in (res.error or "")


@pytest.mark.asyncio
async def test_invoke_transport_error_does_not_raise():
    async with respx.mock(base_url="http://mcphub") as mock:
        mock.post("/v1/tools/shell/invoke").mock(side_effect=httpx.ConnectError("boom"))
        client = McpHubClient("http://mcphub")
        res = await client.invoke("shell", sandbox_id="s", args={})
        await client.close()
    assert res.ok is False
    assert "transport error" in (res.error or "")


@pytest.mark.asyncio
async def test_ensure_sandbox():
    async with respx.mock(base_url="http://mcphub") as mock:
        mock.post("/v1/sandbox").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": "sid",
                    "session_id": "sess",
                    "workdir": "/tmp/sid",
                    "created_at": 1.0,
                    "last_used": 2.0,
                },
            )
        )
        client = McpHubClient("http://mcphub")
        info = await client.ensure_sandbox("sess")
        await client.close()
    assert info["id"] == "sid"
