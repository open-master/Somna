"""Async Postgres pool (asyncpg) — used for raw SQL and event persistence."""

from __future__ import annotations

import asyncpg

from app.config import get_settings
from app.logging_setup import get_logger

log = get_logger(__name__)

_pool: asyncpg.Pool | None = None


def _normalise_dsn(url: str) -> str:
    # SQLAlchemy-style URLs use 'postgresql+asyncpg://'; asyncpg only wants 'postgresql://'
    return url.replace("postgresql+asyncpg://", "postgresql://")


async def init_pool() -> asyncpg.Pool:
    global _pool
    if _pool is not None:
        return _pool
    settings = get_settings()
    dsn = _normalise_dsn(settings.postgres_url)
    _pool = await asyncpg.create_pool(
        dsn=dsn,
        min_size=2,
        max_size=20,
        command_timeout=30,
    )
    log.info("postgres.pool.ready", min=2, max=20)
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
        log.info("postgres.pool.closed")


def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("Postgres pool not initialised — call init_pool() first.")
    return _pool
