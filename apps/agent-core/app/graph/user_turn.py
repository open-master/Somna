"""当前用户回合文本：ingest 会在末条 HumanMessage 上追加附件沙箱路径等信息。"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage, HumanMessage

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


def executor_messages_for_current_turn(state: SessionState) -> list[Any]:
    """Keep conversational history, but scope internal tool traffic to this user turn.

    Checkpoint messages span the whole session. Replaying prior ToolMessages (or the
    AI tool-call messages paired with them) into a new task wastes context and may
    make providers reject an incomplete tool-call chain. Internal messages after the
    latest HumanMessage belong to the current run and must remain available across
    reflect → execute loops.
    """
    messages: list[Any] = list(state.get("messages") or [])
    current_start: int | None = None
    for index in range(len(messages) - 1, -1, -1):
        if isinstance(messages[index], HumanMessage):
            current_start = index
            break
    if current_start is None:
        return messages

    prior: list[Any] = []
    for message in messages[:current_start]:
        if isinstance(message, HumanMessage):
            prior.append(message)
        elif isinstance(message, AIMessage) and not getattr(message, "tool_calls", None):
            prior.append(message)
    return prior + messages[current_start:]


def prior_conversation_text(state: SessionState, *, max_chars: int = 8000) -> str:
    """Return recent user/assistant context before the current human turn.

    Direct-answer mode must still understand follow-up questions such as
    “那他的妻子呢？”. Tool/System messages are intentionally omitted to keep
    the lightweight prompt compact and avoid leaking execution internals.
    """
    messages: list[Any] = list(state.get("messages") or [])
    if messages and isinstance(messages[-1], HumanMessage):
        messages = messages[:-1]

    lines: list[str] = []
    for message in messages:
        if isinstance(message, HumanMessage):
            text = _human_message_body(message).strip()
            if text:
                lines.append(f"用户：{text}")
        elif isinstance(message, AIMessage):
            content = getattr(message, "content", "")
            text = content if isinstance(content, str) else str(content or "")
            text = text.strip()
            if text:
                lines.append(f"助手：{text}")

    if not lines:
        return ""
    blob = "\n".join(lines)
    if len(blob) > max_chars:
        blob = "…\n" + blob[-max_chars:]
    return blob
