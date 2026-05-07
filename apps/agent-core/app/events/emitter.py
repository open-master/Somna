"""Event emitter — publishes AgentEvents to NATS and persists to Postgres.

All LangGraph nodes + Claude SDK callbacks emit via this single facade, so
the transport (NATS) and durability (Postgres) are coupled together.
"""

from __future__ import annotations

import json
from typing import Any

from somna_events import AgentEvent

from app.logging_setup import get_logger
from app.storage.nats_client import get_js
from app.storage.postgres import get_pool

log = get_logger(__name__)


def _subject(session_id: str) -> str:
    return f"session.{session_id}"


async def emit(event: AgentEvent) -> None:
    """Publish + persist a single event. Fire-and-forget on NATS error."""
    payload = event.model_dump(mode="json")
    session_id = str(payload["session_id"])
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    # 1) Persist to Postgres (authoritative for replay)
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO events (session_id, type, payload)
            VALUES ($1::uuid, $2, $3::jsonb)
            RETURNING id
            """,
            session_id,
            payload["type"],
            json.dumps(payload, ensure_ascii=False),
        )
    seq = row["id"]

    payload["seq"] = seq

    # 2) Publish to NATS (live stream)
    try:
        js = get_js()
        await js.publish(_subject(session_id), json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        log.warning("event.publish.failed", error=str(exc), subject=_subject(session_id))


async def fetch_history(session_id: str, since_seq: int = 0, limit: int = 500) -> list[dict[str, Any]]:
    """Replay events for a session from Postgres."""
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id AS seq, type, payload, created_at
            FROM events
            WHERE session_id = $1::uuid AND id > $2
            ORDER BY id ASC
            LIMIT $3
            """,
            session_id,
            since_seq,
            limit,
        )
    out: list[dict[str, Any]] = []
    for r in rows:
        payload = r["payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        row_ts = r["created_at"]
        created = row_ts.isoformat() if hasattr(row_ts, "isoformat") else str(row_ts)
        out.append({**payload, "seq": r["seq"], "created_at": created})
    return out
