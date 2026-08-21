from __future__ import annotations

from uuid import uuid4

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


async def test_invoke_tool_idempotency_replays_success_without_repeating_side_effect(
    client: AsyncClient,
):
    suffix = uuid4().hex
    sandbox_id = f"api_idempotency_{suffix}"
    payload = {
        "sandbox_id": sandbox_id,
        "args": {"cmd": "echo once >> counter.txt && wc -l < counter.txt"},
        "idempotency_key": f"run:test:{suffix}",
    }

    first = await client.post("/v1/tools/shell/invoke", json=payload)
    second = await client.post("/v1/tools/shell/invoke", json=payload)
    count = await client.post(
        "/v1/tools/shell/invoke",
        json={"sandbox_id": sandbox_id, "args": {"cmd": "wc -l < counter.txt"}},
    )

    assert first.status_code == second.status_code == count.status_code == 200
    assert first.json() == second.json()
    assert count.json()["output"]["stdout"].strip() == "1"


async def test_invoke_unknown_tool(client: AsyncClient):
    r = await client.post(
        "/v1/tools/nope/invoke", json={"sandbox_id": "x", "args": {}}
    )
    assert r.status_code == 404
