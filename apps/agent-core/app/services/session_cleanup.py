"""Hard-delete session: DB (with cascades), rag_chunks, S3 objects, MCP sandbox dir."""

from __future__ import annotations

import uuid
from typing import Any

from app.config import get_settings
from app.logging_setup import get_logger
from app.services.attachments import delete_session_uploads_prefix
from app.storage.postgres import get_pool
from app.tools.client import get_client

log = get_logger(__name__)


async def _delete_s3_keys(keys: list[str]) -> None:
    if not keys:
        return
    settings = get_settings()
    endpoint = (settings.s3_endpoint or "").strip()
    bucket = (settings.s3_bucket or "").strip()
    ak = (settings.s3_access_key or "").strip()
    sk = (settings.s3_secret_key or "").strip()
    if not endpoint or not bucket or not ak or not sk:
        log.warning("session.delete.skip_s3", reason="S3 not configured", keys=len(keys))
        return
    try:
        import aioboto3  # type: ignore[import-untyped]
    except ImportError:
        log.warning("session.delete.skip_s3", reason="aioboto3 missing")
        return

    session = aioboto3.Session()
    async with session.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=ak,
        aws_secret_access_key=sk,
    ) as s3c:
        for key in keys:
            if not key:
                continue
            try:
                await s3c.delete_object(Bucket=bucket, Key=key)
            except Exception as exc:  # noqa: BLE001
                log.warning("session.delete.s3_object_failed", key=key, error=str(exc))


async def _delete_mcp_sandbox(session_id: uuid.UUID) -> None:
    await get_client().delete_sandbox(str(session_id))


async def hard_delete_session(*, session_id: uuid.UUID, user_id: uuid.UUID) -> None:
    """Assert session belongs to user, not running; delete satellite data then session row."""
    from fastapi import HTTPException

    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "SELECT id, user_id, status FROM sessions WHERE id = $1 FOR UPDATE",
                session_id,
            )
            if row is None:
                raise HTTPException(status_code=404, detail="session not found")
            if row["user_id"] != user_id:
                raise HTTPException(status_code=404, detail="session not found")
            if row["status"] == "running":
                raise HTTPException(
                    status_code=409,
                    detail="cannot delete session while a run is in progress; interrupt or wait until idle",
                )
            key_rows = await conn.fetch("SELECT s3_key FROM artifacts WHERE session_id = $1", session_id)
            s3_keys = [r["s3_key"] for r in key_rows if r.get("s3_key")]

    await _delete_s3_keys(s3_keys)
    await delete_session_uploads_prefix(session_id)
    await _delete_mcp_sandbox(session_id)

    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "SELECT id, user_id, status FROM sessions WHERE id = $1 FOR UPDATE",
                session_id,
            )
            if row is None:
                return
            if row["user_id"] != user_id or row["status"] == "running":
                raise HTTPException(status_code=409, detail="session state changed; retry")
            await conn.execute("DELETE FROM rag_chunks WHERE session_id = $1", session_id)
            await conn.execute("DELETE FROM sandboxes WHERE session_id = $1", session_id)
            await conn.execute("DELETE FROM sessions WHERE id = $1", session_id)
    log.info("session.hard_deleted", session_id=str(session_id), user_id=str(user_id))


async def assert_session_owner(session_id: uuid.UUID, user_id: uuid.UUID) -> dict[str, Any]:
    """Return session row as dict or raise 404."""
    from fastapi import HTTPException

    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM sessions WHERE id = $1", session_id)
    if row is None or row["user_id"] != user_id:
        raise HTTPException(status_code=404, detail="session not found")
    return dict(row)
