"""mem0 client wrapper.

Why wrap mem0:
- Centralise config assembly from Settings.
- Keep all calls off the event loop (`asyncio.to_thread`) because mem0's
  sync client performs blocking network I/O.
- Make search failures non-fatal: if mem0 / milvus is down, we log once
  and return empty results so the agent keeps running.

The read path is wired into planner / executor prompts, and `add_memory`
is used by finalize to persist successful exchanges for future sessions.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from app.config import get_settings
from app.logging_setup import get_logger

log = get_logger(__name__)

_instance: Any | None = None
_init_attempted = False
_init_failed_reason: str | None = None


@dataclass(slots=True)
class MemoryItem:
    id: str
    text: str
    score: float | None = None
    metadata: dict[str, Any] | None = None


def is_enabled() -> bool:
    return bool(get_settings().memory_enabled)


def _build_config() -> dict[str, Any]:
    s = get_settings()
    return {
        "llm": {
            "provider": s.mem0_llm_provider,
            "config": {
                "model": s.mem0_llm_model,
                "api_base": s.mem0_llm_base_url,
                "api_key": s.litellm_master_key,
            },
        },
        "embedder": {
            "provider": s.mem0_llm_provider,
            "config": {
                "model": s.mem0_embedder_model,
                "api_base": s.mem0_llm_base_url,
                "api_key": s.litellm_master_key,
            },
        },
        "vector_store": {
            "provider": s.mem0_vector_store,
            "config": {
                "host": s.milvus_host,
                "port": s.milvus_port,
                "collection_name": s.mem0_collection,
            },
        },
    }


def _init_sync() -> Any | None:
    """Instantiate mem0.Memory. Returns None if mem0 isn't installed or config fails."""
    global _init_failed_reason  # noqa: PLW0603
    try:
        from mem0 import Memory  # type: ignore[import-untyped]
    except ImportError as exc:
        _init_failed_reason = f"mem0 import failed: {exc}"
        log.warning("memory.disabled.no_import", error=str(exc))
        return None
    try:
        return Memory.from_config(_build_config())
    except Exception as exc:  # noqa: BLE001
        _init_failed_reason = f"mem0 init failed: {exc}"
        log.warning("memory.disabled.init_failed", error=str(exc))
        return None


async def get_memory() -> Any | None:
    """Lazily initialise the mem0 client. Thread-safe enough for dev."""
    global _instance, _init_attempted  # noqa: PLW0603
    if not is_enabled():
        return None
    if _init_attempted:
        return _instance
    _init_attempted = True
    _instance = await asyncio.to_thread(_init_sync)
    return _instance


def _user_id_from(session_id: str, user_id: str | None) -> str:
    """Prefer the authenticated user; fall back to per-session namespace."""
    return user_id or f"session:{session_id}"


async def search_memories(
    query: str,
    *,
    session_id: str,
    user_id: str | None = None,
    top_k: int | None = None,
) -> list[MemoryItem]:
    mem = await get_memory()
    if mem is None or not query.strip():
        return []
    k = top_k or get_settings().memory_top_k
    uid = _user_id_from(session_id, user_id)

    def _do_search() -> Any:
        return mem.search(query=query, user_id=uid, limit=k)

    try:
        raw = await asyncio.to_thread(_do_search)
    except Exception as exc:  # noqa: BLE001
        log.warning("memory.search_failed", error=str(exc), user_id=uid)
        return []

    return _coerce_items(raw)[:k]


async def add_memory(
    text: str,
    *,
    session_id: str,
    user_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> bool:
    mem = await get_memory()
    if mem is None or not text.strip():
        return False
    uid = _user_id_from(session_id, user_id)

    def _do_add() -> Any:
        return mem.add(
            messages=[{"role": "user", "content": text}],
            user_id=uid,
            metadata=metadata or {},
        )

    try:
        await asyncio.to_thread(_do_add)
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("memory.add_failed", error=str(exc), user_id=uid)
        return False


def _coerce_items(raw: Any) -> list[MemoryItem]:
    """mem0 returns either {results: [...]} or a bare list depending on version."""
    if raw is None:
        return []
    items = raw.get("results") if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        return []
    out: list[MemoryItem] = []
    for entry in items:
        if not isinstance(entry, dict):
            continue
        text = entry.get("memory") or entry.get("text") or ""
        if not text:
            continue
        out.append(
            MemoryItem(
                id=str(entry.get("id") or len(out)),
                text=str(text),
                score=_as_float(entry.get("score")),
                metadata=entry.get("metadata"),
            )
        )
    return out


def _as_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def format_memories(items: list[MemoryItem], *, limit: int = 5) -> str:
    """Render memories as compact bullet points for prompt injection."""
    if not items:
        return ""
    lines = []
    for i, it in enumerate(items[:limit]):
        snippet = it.text.replace("\n", " ").strip()
        if len(snippet) > 240:
            snippet = snippet[:237] + "..."
        lines.append(f"- {snippet}")
    return "\n".join(lines)
