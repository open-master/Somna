"""HTTP client for MCP Hub.

All Agent Core tool calls go through this facade. Keeps the rest of the
codebase free from HTTP details, and lets us swap the transport later
(direct MCP stdio / SSE) without changing call sites.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.config import get_settings
from app.logging_setup import get_logger

log = get_logger(__name__)


class ToolManifest(BaseModel):
    """Mirror of mcp-hub's ToolManifest."""

    model_config = ConfigDict(extra="allow")

    name: str
    description: str
    input_schema: dict[str, Any]
    category: str = "general"
    mutates: bool = False
    version: str = "1"


class ToolResult(BaseModel):
    """Mirror of mcp-hub's ToolResult."""

    model_config = ConfigDict(extra="allow")

    ok: bool
    preview: str = ""
    full_ref: str | None = None
    output: Any = None
    duration_ms: int | None = None
    error: str | None = None


class McpHubClient:
    """Thin async wrapper around MCP Hub's REST API."""

    def __init__(self, base_url: str, *, timeout: float = 120.0) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(base_url=self.base_url, timeout=timeout)

    async def close(self) -> None:
        await self._client.aclose()

    # ---------- tool registry ----------

    async def list_tools(self) -> list[ToolManifest]:
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=0.4, max=2),
            retry=retry_if_exception_type(httpx.HTTPError),
            reraise=True,
        ):
            with attempt:
                r = await self._client.get("/v1/tools")
                r.raise_for_status()
        data = r.json()
        return [ToolManifest(**m) for m in data]

    # ---------- sandbox ----------

    async def ensure_sandbox(self, session_id: str) -> dict[str, Any]:
        """Create-or-return a sandbox keyed by session id."""
        r = await self._client.post("/v1/sandbox", json={"session_id": session_id})
        r.raise_for_status()
        return r.json()

    # ---------- invoke ----------

    async def invoke(
        self,
        name: str,
        *,
        sandbox_id: str,
        args: dict[str, Any],
        session_id: str | None = None,
        run_id: str | None = None,
        timeout: float | None = None,
    ) -> ToolResult:
        payload = {"sandbox_id": sandbox_id, "args": args}
        if session_id:
            payload["session_id"] = session_id
        if run_id:
            payload["run_id"] = run_id
        try:
            r = await self._client.post(
                f"/v1/tools/{name}/invoke",
                json=payload,
                timeout=timeout or self._client.timeout,
            )
        except httpx.HTTPError as exc:
            log.warning("tool.invoke.http_error", tool=name, error=str(exc))
            return ToolResult(ok=False, error=f"mcp-hub transport error: {exc}")
        if r.status_code == 404:
            return ToolResult(ok=False, error=f"tool {name!r} not registered on mcp-hub")
        if r.status_code >= 400:
            return ToolResult(ok=False, error=f"mcp-hub status {r.status_code}: {r.text[:200]}")
        return ToolResult(**r.json())


@lru_cache
def get_client() -> McpHubClient:
    return McpHubClient(get_settings().mcp_hub_url)


async def close_client() -> None:
    info = get_client.cache_info()
    if info.currsize:
        c = get_client()
        await c.close()
        get_client.cache_clear()
