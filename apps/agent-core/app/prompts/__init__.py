"""Prompt template loader — reads `packages/prompts/<role>/<version>.md`."""

from .loader import build_system_prompt, load_template

__all__ = ["build_system_prompt", "load_template"]
