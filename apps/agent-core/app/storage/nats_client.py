"""NATS JetStream client."""

from __future__ import annotations

import nats
from nats.aio.client import Client as NATS
from nats.js import JetStreamContext

from app.config import get_settings
from app.logging_setup import get_logger

log = get_logger(__name__)

_nc: NATS | None = None
_js: JetStreamContext | None = None


async def init_nats() -> tuple[NATS, JetStreamContext]:
    global _nc, _js
    if _nc is not None and _js is not None:
        return _nc, _js
    settings = get_settings()
    _nc = await nats.connect(servers=[settings.nats_url], max_reconnect_attempts=-1)
    _js = _nc.jetstream()

    try:
        await _js.add_stream(
            name="somna-sessions",
            subjects=["session.>"],
            max_msgs=1_000_000,
            max_age=86_400 * 7,  # 7 days
        )
        log.info("nats.stream.ready", name="somna-sessions")
    except Exception as exc:  # noqa: BLE001
        log.info("nats.stream.exists_or_failed", error=str(exc))

    return _nc, _js


async def close_nats() -> None:
    global _nc
    if _nc is not None:
        await _nc.drain()
        _nc = None
        log.info("nats.closed")


def get_js() -> JetStreamContext:
    if _js is None:
        raise RuntimeError("NATS JetStream not initialised — call init_nats() first.")
    return _js


def is_connected() -> bool:
    return bool(_nc and _nc.is_connected)
