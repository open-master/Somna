"""当前用户回合文本：ingest 会在末条 HumanMessage 上追加附件沙箱路径等信息。"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import HumanMessage

from app.graph.state import SessionState


def _human_message_body(m: HumanMessage) -> str:
    c: Any = m.content
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        parts: list[str] = []
        for p in c:
            if isinstance(p, dict) and p.get("type") == "text":
                parts.append(str(p.get("text", "")))
            elif isinstance(p, dict) and p.get("type") == "image_url":
                parts.append("[图片]")
            else:
                parts.append(str(p))
        return "\n".join(parts)
    return str(c)


def last_human_turn_text(state: SessionState) -> str:
    """取本轮用户输入全文（含 ingest 追加的附件说明），勿用原始的 `user_message` 字段。"""
    msgs: list[Any] = list(state.get("messages") or [])
    for m in reversed(msgs):
        if isinstance(m, HumanMessage):
            return _human_message_body(m).strip()
    raw = state.get("user_message")
    return (raw if isinstance(raw, str) else str(raw or "")).strip()
