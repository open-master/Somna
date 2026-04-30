"""Long-term memory (mem0) integration.

The agent talks to mem0 via a thin wrapper so nodes can query/record
memories without knowing the underlying vector store or embedder config.
All failures degrade gracefully — missing memory should never block a
run.
"""

from app.memory.client import (
    MemoryItem,
    add_memory,
    format_memories,
    get_memory,
    is_enabled,
    search_memories,
)

__all__ = [
    "MemoryItem",
    "add_memory",
    "format_memories",
    "get_memory",
    "is_enabled",
    "search_memories",
]
