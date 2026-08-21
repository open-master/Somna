"""Persist the last run phase on sessions so the sidebar survives refresh."""

from __future__ import annotations

from uuid import UUID

from app.storage.postgres import get_pool


async def ensure_session_phase_columns() -> None:
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute("ALTER TABLE sessions ADD COLUMN IF NOT EXISTS last_phase TEXT")


async def mark_session_run_closed(
    session_id: UUID,
    *,
    status: str,
    last_phase: str | None,
) -> None:
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE sessions
            SET status = $1,
                last_phase = $2,
                workflow_id = NULL,
                run_id = NULL,
                updated_at = now()
            WHERE id = $3
            """,
            status,
            last_phase,
            session_id,
        )
