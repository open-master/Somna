"""Shared tool-catalog and planner hint policy.

Planner and executor must reason over the same executable catalog.  Planner
`tool_hint` values are model output, so they are normalized and validated at
the plan boundary instead of becoming an unchecked scheduler permission.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from app.tools.client import ToolManifest

_TOOL_HINT_SPLIT_RE = re.compile(r"[\s,;|/]+")
_TOOL_HINT_ALIASES: dict[str, tuple[str, ...]] = {
    "browser": ("search", "browser"),
    "browser_read": ("browser", "search"),
    "web": ("search",),
    "file": ("filesystem",),
    "file_read": ("filesystem",),
    "file_write": ("filesystem",),
    "code": ("shell",),
    "code_exec": ("shell",),
    "rag": ("search",),
}
_EMPTY_HINTS = {"", "null", "none", "nil", "n/a"}


def manifests_allowed_by_task_frame(
    manifests: list[ToolManifest], task_frame: dict[str, Any] | None
) -> list[ToolManifest]:
    """Apply the Task Frame action scope as the executable allow-list."""
    raw = (task_frame or {}).get("allowed_action_scope") if isinstance(task_frame, dict) else None
    allowed = {str(item).strip().lower() for item in raw or [] if str(item).strip()}
    if not allowed:
        return manifests

    out: list[ToolManifest] = []
    has_browser_tool = any((manifest.category or "").lower() == "browser" for manifest in manifests)
    for manifest in manifests:
        name = manifest.name
        category = (manifest.category or "").lower()
        if name == "shell":
            permitted = "shell" in allowed
        elif name == "filesystem" or category == "file":
            permitted = bool({"file_read", "file_write"} & allowed)
        elif name == "search" or category == "net":
            permitted = "search" in allowed or ("browser_read" in allowed and not has_browser_tool)
        elif category == "media":
            permitted = "media" in allowed
        elif category == "browser":
            permitted = "browser_read" in allowed
        else:
            permitted = False
        if not permitted:
            continue

        if name == "filesystem" and "file_write" not in allowed:
            restricted = manifest.model_copy(deep=True)
            schema = dict(restricted.input_schema or {})
            properties = dict(schema.get("properties") or {})
            action = dict(properties.get("action") or {})
            action["enum"] = ["read", "list", "stat"]
            properties["action"] = action
            schema["properties"] = properties
            restricted.input_schema = schema
            out.append(restricted)
        else:
            out.append(manifest)
    return out


def _raw_hint_tokens(raw: Any) -> list[str]:
    values = raw if isinstance(raw, (list, tuple, set)) else [raw]
    out: list[str] = []
    for value in values:
        for token in _TOOL_HINT_SPLIT_RE.split(str(value or "").strip().lower()):
            if token not in _EMPTY_HINTS and token not in out:
                out.append(token)
    return out


def normalize_tool_hint(
    raw: Any,
    manifests: Iterable[ToolManifest] | None = None,
) -> str | None:
    """Return exact executable tool names separated by ``|``.

    Old planner aliases are mapped to current MCP names.  When a manifest
    catalog is supplied, names that cannot execute in this task are dropped.
    """
    available = {manifest.name for manifest in manifests} if manifests is not None else None
    normalized: list[str] = []
    for token in _raw_hint_tokens(raw):
        candidates = _TOOL_HINT_ALIASES.get(token, (token,))
        chosen = next(
            (candidate for candidate in candidates if available is None or candidate in available),
            None,
        )
        if chosen and chosen not in normalized:
            normalized.append(chosen)
    return "|".join(normalized) or None


def tool_hint_tokens(raw: Any) -> set[str]:
    normalized = normalize_tool_hint(raw)
    return set(normalized.split("|")) if normalized else set()
