"""SSE stream — clients subscribe to a session's live event stream.

Combines Postgres replay (since=<seq>) with live NATS JetStream push so the
browser can reconnect without losing events.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Depends, Query, Request
from sse_starlette.sse import EventSourceResponse

from app.api.deps import CurrentUser, get_current_user
from app.events.emitter import fetch_history
from app.logging_setup import get_logger
from app.services.session_cleanup import assert_session_owner
from app.storage.nats_client import get_js

log = get_logger(__name__)
router = APIRouter(prefix="/v1/sessions", tags=["stream"])


async def _replay_then_live(
    session_id: uuid.UUID,
    since_seq: int,
    request: Request,
) -> AsyncGenerator[dict, None]:
    # --- 1) Replay persisted history first ---
    history = await fetch_history(str(session_id), since_seq=since_seq, limit=10_000)
    last_seq = since_seq
    for ev in history:
        if await request.is_disconnected():
            return
        yield {"event": ev.get("type", "message"), "id": str(ev["seq"]), "data": json.dumps(ev, ensure_ascii=False)}
        last_seq = ev["seq"]

    # While replay was streaming to a slow client, new rows may have been INSERTed
    # into `events` but NATS was not subscribed yet — those publishes are missed
    # by this connection. Drain Postgres until caught up before tailing NATS.
    while True:
        catchup = await fetch_history(str(session_id), since_seq=last_seq, limit=10_000)
        if not catchup:
            break
        for ev in catchup:
            if await request.is_disconnected():
                return
            yield {"event": ev.get("type", "message"), "id": str(ev["seq"]), "data": json.dumps(ev, ensure_ascii=False)}
            last_seq = ev["seq"]

    # --- 2) Then tail NATS JetStream for live events ---
    js = get_js()
    subject = f"session.{session_id}"
    try:
        sub = await js.subscribe(subject, durable=None)
    except Exception as exc:  # noqa: BLE001
        log.warning("sse.subscribe.failed", session_id=str(session_id), error=str(exc))
        return

    try:
        while True:
            if await request.is_disconnected():
                break
            try:
                msg = await asyncio.wait_for(sub.next_msg(timeout=15), timeout=20)
            except asyncio.TimeoutError:
                # keep-alive comment frame
                yield {"event": "ping", "data": "keep-alive"}
                continue
            if msg is None:
                continue
            try:
                data = json.loads(msg.data.decode("utf-8"))
            except Exception:  # noqa: BLE001
                await msg.ack()
                continue
            seq = data.get("seq", last_seq + 1)
            if seq <= last_seq:
                await msg.ack()
                continue
            last_seq = seq
            yield {"event": data.get("type", "message"), "id": str(seq), "data": json.dumps(data, ensure_ascii=False)}
            await msg.ack()
    finally:
        try:
            await sub.unsubscribe()
        except Exception:  # noqa: BLE001
            pass


@router.get("/{sid}/stream")
async def stream(
    sid: uuid.UUID,
    request: Request,
    since: int = Query(default=0, ge=0),
    user: CurrentUser = Depends(get_current_user),
) -> EventSourceResponse:
    await assert_session_owner(sid, user.id)
    return EventSourceResponse(_replay_then_live(sid, since, request))
