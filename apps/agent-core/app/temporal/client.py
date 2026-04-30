from __future__ import annotations

from temporalio.client import Client

from app.config import get_settings

_client: Client | None = None


async def get_temporal_client() -> Client:
    global _client
    if _client is not None:
        return _client
    settings = get_settings()
    _client = await Client.connect(
        settings.temporal_host,
        namespace=settings.temporal_namespace,
    )
    return _client


async def close_temporal_client() -> None:
    global _client
    _client = None
