"""Context compaction helper.

Used at the top of each execute turn: if the working message list grows past
`agent_compact_token_threshold` (approximated by character count), an older
slice is summarised via the `agent-cheap` model and replaced with a single
system message. The last `KEEP_TAIL` messages are always kept intact so the
model can still see the most recent tool invocation and its result.
"""

from __future__ import annotations

import hashlib
from typing import Iterable

from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from somna_events import SessionPhase, StatusEvent

from app.config import get_settings
from app.events.emitter import emit
from app.llm.client import get_async_openai
from app.logging_setup import get_logger
from app.prompts.loader import load_template, render
from app.services.billing import emit_model_usage

log = get_logger(__name__)

# ~4 chars per token is a conservative approximation across Chinese/English.
_CHARS_PER_TOKEN = 4
# Always preserve the last N messages verbatim (the "live tail").
KEEP_TAIL = 6


def _content_chars(messages: Iterable) -> int:
    total = 0
    for m in messages:
        c = getattr(m, "content", "") or ""
        total += len(c) if isinstance(c, str) else len(str(c))
    return total


def estimate_tokens(messages: Iterable) -> int:
    return _content_chars(messages) // _CHARS_PER_TOKEN


def _format_history(messages: Iterable) -> str:
    lines: list[str] = []
    for m in messages:
        if isinstance(m, SystemMessage):
            role = "system"
        elif isinstance(m, HumanMessage):
            role = "user"
        elif isinstance(m, AIMessage):
            role = "assistant"
        elif isinstance(m, ToolMessage):
            role = f"tool({getattr(m, 'name', '')})"
        else:
            role = type(m).__name__.lower()
        content = m.content if isinstance(m.content, str) else str(m.content)
        lines.append(f"[{role}] {content[:2000]}")
    return "\n".join(lines)


def resolve_compact_llm_model(
    longctx_model: str | None,
    compact_model: str | None,
) -> str:
    """Long-context role overrides cheap/compact for summarisation; then env defaults."""
    settings = get_settings()
    for raw in (
        longctx_model,
        compact_model,
        settings.agent_default_longctx,
        settings.agent_compact_model,
    ):
        if raw and str(raw).strip():
            return str(raw).strip()
    return settings.agent_compact_model


async def maybe_compact(
    messages: list,
    *,
    session_id,
    run_id,
    compact_model: str | None = None,
    longctx_model: str | None = None,
    billing_enabled: bool = False,
) -> tuple[list, bool, str | None]:
    """Summarise old messages in place. Returns (new_messages, did_compact, summary)."""
    settings = get_settings()
    threshold = settings.agent_compact_token_threshold
    if not messages or estimate_tokens(messages) < threshold:
        return messages, False, None
    if len(messages) <= KEEP_TAIL + 2:
        # Not enough to compress meaningfully.
        return messages, False, None

    head = messages[:-KEEP_TAIL]
    tail = messages[-KEEP_TAIL:]

    template = load_template("compact", "v1")
    if not template:
        log.warning("compact.template_missing")
        return messages, False, None

    prompt = render(template, history=_format_history(head))

    client = get_async_openai()
    model = resolve_compact_llm_model(longctx_model, compact_model)
    log.info(
        "graph.compact.start",
        session_id=str(session_id),
        run_id=run_id,
        model=model,
        head_messages=len(head),
    )
    try:
        resp = await client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            stream=False,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("compact.llm_failed", error=str(exc))
        return messages, False, None

    summary = (resp.choices[0].message.content or "").strip()
    usage = getattr(resp, "usage", None)
    usage_input = int(getattr(usage, "prompt_tokens", 0) or 0)
    usage_output = int(getattr(usage, "completion_tokens", 0) or 0)
    if billing_enabled and (usage_input or usage_output):
        await emit_model_usage(
            session_id=session_id,
            run_id=run_id,
            usage_key=f"{run_id}:compact:{hashlib.sha256(prompt.encode('utf-8')).hexdigest()[:16]}",
            phase="compact",
            model=model,
            input_tokens=usage_input,
            output_tokens=usage_output,
        )
    if not summary:
        return messages, False, None

    summary_msg = SystemMessage(
        content="# 历史会话摘要（由 compact 节点生成）\n\n" + summary
    )
    await emit(
        StatusEvent(
            session_id=session_id,
            run_id=run_id,
            phase=SessionPhase.executing,
            message=f"已压缩历史上下文 ({len(head)} 条 → 1 条摘要)",
        )
    )
    log.info(
        "compact.done",
        session_id=str(session_id),
        run_id=run_id,
        model=model,
        compressed=len(head),
        tail=len(tail),
        summary_chars=len(summary),
    )
    return [summary_msg, *tail], True, summary
