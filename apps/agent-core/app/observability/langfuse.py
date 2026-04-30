"""Langfuse client (lazy, optional in dev)."""

from __future__ import annotations

from functools import lru_cache

from app.config import get_settings
from app.logging_setup import get_logger

log = get_logger(__name__)


@lru_cache
def get_langfuse():
    """Return a Langfuse client if keys are configured, else None."""
    s = get_settings()
    if not (s.langfuse_host and s.langfuse_public_key and s.langfuse_secret_key):
        log.info("langfuse.disabled", reason="missing keys")
        return None
    try:
        from langfuse import Langfuse
    except ImportError:
        log.warning("langfuse.import_failed")
        return None

    client = Langfuse(
        host=s.langfuse_host,
        public_key=s.langfuse_public_key,
        secret_key=s.langfuse_secret_key,
    )
    log.info("langfuse.ready", host=s.langfuse_host)
    return client
