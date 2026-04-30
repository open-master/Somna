"""Global tool registry — maps tool name → instance.

Tools register themselves at import time via `@register` decorator.
"""

from __future__ import annotations

from typing import TypeVar

from .base import BaseTool

_T = TypeVar("_T", bound=BaseTool)

_REGISTRY: dict[str, BaseTool] = {}


def register(cls: type[_T]) -> type[_T]:
    """Decorator: instantiate and register a tool class."""
    if not cls.name:
        raise ValueError(f"Tool {cls.__name__} must define `name`")
    tool = cls()
    if tool.name in _REGISTRY:
        raise ValueError(f"Tool {tool.name!r} already registered")
    _REGISTRY[tool.name] = tool
    return cls


def get(name: str) -> BaseTool | None:
    return _REGISTRY.get(name)


def all_tools() -> list[BaseTool]:
    return list(_REGISTRY.values())


def clear() -> None:
    """Testing only."""
    _REGISTRY.clear()
