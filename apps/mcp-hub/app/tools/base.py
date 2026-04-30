"""Base tool abstraction.

Each tool declares a JSON-schema for its arguments and implements `invoke()`.
Results are always wrapped in `ToolResult` so Agent Core has a uniform shape
regardless of underlying tool.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ToolManifest(BaseModel):
    """Manifest exposed via `GET /v1/tools`."""

    name: str
    description: str
    input_schema: dict[str, Any] = Field(
        description="JSON Schema (Draft 2020-12) for the `args` payload."
    )
    # Category helps UI grouping (shell / file / net / code / ...)
    category: str = "general"
    # Whether tool mutates sandbox state (used for UI / rollback hints)
    mutates: bool = False
    version: str = "1"


class ToolResult(BaseModel):
    """Uniform response returned from every tool invocation."""

    model_config = ConfigDict(extra="allow")

    ok: bool
    preview: str = ""
    full_ref: str | None = None
    output: Any = None
    duration_ms: int | None = None
    error: str | None = None


class ToolContext(BaseModel):
    """Per-invocation context passed to tools."""

    sandbox_id: str
    workdir: str
    session_id: str | None = None
    run_id: str | None = None


class BaseTool(ABC):
    """Base class for all tools."""

    # Required subclass-level attributes
    name: str = ""
    description: str = ""
    category: str = "general"
    mutates: bool = False
    version: str = "1"
    input_schema: dict[str, Any] = {}

    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name=self.name,
            description=self.description,
            input_schema=self.input_schema,
            category=self.category,
            mutates=self.mutates,
            version=self.version,
        )

    @abstractmethod
    async def invoke(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        """Execute and return a ToolResult. Must not raise for known errors."""
