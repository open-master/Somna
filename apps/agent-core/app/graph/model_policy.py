"""How per-role model settings map onto concrete LLM calls in the session graph."""

from __future__ import annotations

from typing import Any, Protocol, Sequence

from langchain_core.messages import AIMessage, ToolMessage


class _ManifestLike(Protocol):
    name: str
    category: str


def _tool_names_from_ai_message(m: AIMessage) -> list[str]:
    raw = getattr(m, "tool_calls", None) or m.additional_kwargs.get("tool_calls") or []
    names: list[str] = []
    if not raw:
        return names
    for c in raw:
        if isinstance(c, dict):
            if c.get("type") == "function" and isinstance(c.get("function"), dict):
                n = c["function"].get("name")
            else:
                n = c.get("name")
            if n:
                names.append(str(n))
        else:
            n = getattr(c, "name", None)
            if n:
                names.append(str(n))
    return names


def last_round_tool_names(messages: Sequence[Any]) -> list[str]:
    """Tool names from the assistant message that precedes trailing tool results."""
    i = len(messages) - 1
    while i >= 0 and isinstance(messages[i], ToolMessage):
        i -= 1
    if i < 0:
        return []
    m = messages[i]
    if isinstance(m, AIMessage):
        return _tool_names_from_ai_message(m)
    return []


def tool_prefers_coder_model(name: str, manifest: _ManifestLike | None) -> bool:
    if manifest is not None and (manifest.category or "").lower() in ("file", "os", "code"):
        return True
    low = name.lower()
    return any(k in low for k in ("shell", "filesystem", "patch", "write", "str_replace"))


def pick_executor_turn_model(
    *,
    working_messages: Sequence[Any],
    manifest_by_name: dict[str, _ManifestLike],
    executor_model: str,
    coder_model: str,
) -> str:
    """First turn and post-search turns use executor; after file/shell tools use coder (if distinct)."""
    ex = (executor_model or "").strip()
    cd = (coder_model or "").strip()
    if not cd or cd == ex:
        return ex or cd
    names = last_round_tool_names(working_messages)
    if not names:
        return ex
    for n in names:
        if tool_prefers_coder_model(n, manifest_by_name.get(n)):
            return cd
    return ex
