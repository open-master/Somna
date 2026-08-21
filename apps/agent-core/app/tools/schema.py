"""Convert MCP tool manifests to OpenAI function-calling schema.

Agent Core passes the result of `manifests_to_openai_tools()` to the LLM on
every call. A small in-process cache holds the manifest list refreshed at
service boot (and optionally on demand).
"""

from __future__ import annotations

import re
from typing import Any

from app.logging_setup import get_logger

from .client import ToolManifest, get_client

log = get_logger(__name__)

# Anthropic/OpenAI schema requires [a-zA-Z0-9_-]{1,64}
_NAME_RE = re.compile(r"[^a-zA-Z0-9_-]")

_CACHE: dict[str, ToolManifest] = {}


def _safe_name(name: str) -> str:
    return _NAME_RE.sub("_", name)[:64] or "tool"


def manifest_to_openai(manifest: ToolManifest) -> dict[str, Any]:
    schema = dict(manifest.input_schema or {"type": "object", "properties": {}})
    # OpenAI requires `type: object` at the top level and a `properties` key.
    if schema.get("type") != "object":
        schema = {"type": "object", "properties": {}}
    schema.setdefault("properties", {})
    return {
        "type": "function",
        "function": {
            "name": _safe_name(manifest.name),
            "description": manifest.description[:1024],
            "parameters": schema,
        },
    }


def manifests_to_openai_tools(manifests: list[ToolManifest]) -> list[dict[str, Any]]:
    return [manifest_to_openai(m) for m in manifests]


def tool_manifest_cache() -> dict[str, ToolManifest]:
    return dict(_CACHE)


async def refresh_cache() -> list[ToolManifest]:
    """Pull latest manifests from MCP Hub into the in-process cache."""
    client = get_client()
    manifests = await client.list_tools()
    _CACHE.clear()
    for m in manifests:
        _CACHE[m.name] = m
    log.info("tools.cache.refresh", count=len(_CACHE), names=list(_CACHE.keys()))
    return manifests


def openai_tool_choice(force: str | None = None) -> str | dict[str, Any]:
    """Helper for `tool_choice` parameter.

    - None   → "auto"
    - name   → force a specific function
    """
    if force is None:
        return "auto"
    return {"type": "function", "function": {"name": _safe_name(force)}}


def clear_cache_for_tests() -> None:
    _CACHE.clear()
