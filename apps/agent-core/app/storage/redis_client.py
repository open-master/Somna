"""Redis client (aio-redis)."""

from __future__ import annotations

import redis.asyncio as redis

from app.config import get_settings
from app.logging_setup import get_logger

log = get_logger(__name__)

_client: redis.Redis | None = None


async def init_redis() -> redis.Redis:
    global _client
    if _client is not None:
        return _client
    settings = get_settings()
    _client = redis.from_url(settings.redis_url, encoding="utf-8", decode_responses=True)
    await _client.ping()
    log.info("redis.ready")
    return _client


async def close_redis() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
        log.info("redis.closed")


def get_redis() -> redis.Redis:
    if _client is None:
        raise RuntimeError("Redis not initialised — call init_redis() first.")
    return _client
