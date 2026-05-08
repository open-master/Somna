"""Merge MCP Hub tool default models (browser overrides + agent-core env defaults)."""

from __future__ import annotations

from typing import Any

from app.config import Settings


def default_mcp_tool_models(settings: Settings) -> dict[str, str]:
    return {
        "visual_critique": settings.agent_default_visual_critique,
        "wan_text2image": settings.agent_default_mcp_wan_t2i,
        "wan_t2v": settings.agent_default_mcp_wan_t2v,
        "wan_i2v": settings.agent_default_mcp_wan_i2v,
        "wan_r2v": settings.agent_default_mcp_wan_r2v,
        "wan_video_edit": settings.agent_default_mcp_wan_video_edit,
        "minimax_tts": settings.agent_default_mcp_minimax_tts,
    }


def merge_mcp_tool_models(settings: Settings, overrides: dict[str, Any] | None) -> dict[str, str]:
    base = default_mcp_tool_models(settings)
    if not overrides:
        return base
    for k, v in overrides.items():
        if isinstance(v, str) and v.strip():
            base[str(k)] = v.strip()
    return base
