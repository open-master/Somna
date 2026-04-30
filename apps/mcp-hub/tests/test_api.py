from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_healthz(client: AsyncClient):
    r = await client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["service"] == "mcp-hub"


async def test_list_tools(client: AsyncClient):
    r = await client.get("/v1/tools")
    assert r.status_code == 200
    names = [t["name"] for t in r.json()]
    assert "shell" in names and "filesystem" in names and "search" in names


async def test_sandbox_crud(client: AsyncClient):
    r = await client.post("/v1/sandbox", json={"session_id": "api_alpha"})
    assert r.status_code == 200
    info = r.json()
    assert info["id"] == "api_alpha"

    r = await client.get(f"/v1/sandbox/{info['id']}")
    assert r.status_code == 200

    r = await client.delete(f"/v1/sandbox/{info['id']}")
    assert r.status_code == 200 and r.json()["status"] == "deleted"


async def test_invoke_shell(client: AsyncClient):
    r = await client.post(
        "/v1/tools/shell/invoke",
        json={"sandbox_id": "api_shell", "args": {"cmd": "echo ok"}},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert "ok" in body["output"]["stdout"]


async def test_invoke_unknown_tool(client: AsyncClient):
    r = await client.post(
        "/v1/tools/nope/invoke", json={"sandbox_id": "x", "args": {}}
    )
    assert r.status_code == 404
