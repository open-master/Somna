"""In-flight run registry.

Tracks the currently running asyncio.Task per session so the HTTP layer
can cancel it when the user clicks "interrupt" on the UI. Designed for
single-process dev; swap for a Temporal workflow lookup in M3+.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from typing import Optional
from uuid import UUID

from app.logging_setup import get_logger

log = get_logger(__name__)


@dataclass
class InflightRun:
    session_id: UUID
    run_id: str
    task: asyncio.Task
    reason: str | None = None  # populated when cancelled


class _Registry:
    def __init__(self) -> None:
        self._runs: dict[str, InflightRun] = {}
        self._lock = asyncio.Lock()

    async def register(self, session_id: UUID, run_id: str, task: asyncio.Task) -> None:
        key = str(session_id)
        async with self._lock:
            # Overwriting should be rare (the previous one should have been
            # completed/cancelled by the time a new run starts). Log if it
            # happens so we can spot leaks.
            existing = self._runs.get(key)
            if existing and not existing.task.done():
                log.warning(
                    "runtime.registry.overwrite",
                    session_id=key,
                    old_run=existing.run_id,
                    new_run=run_id,
                )
            self._runs[key] = InflightRun(session_id=session_id, run_id=run_id, task=task)

    async def _remove(self, session_id: UUID) -> None:
        async with self._lock:
            self._runs.pop(str(session_id), None)

    def attach_cleanup(self, session_id: UUID) -> None:
        """Best-effort cleanup: schedule removal when the task is done."""
        key = str(session_id)
        run = self._runs.get(key)
        if run is None:
            return

        def _done(_t: asyncio.Task) -> None:
            asyncio.create_task(self._remove(session_id))

        run.task.add_done_callback(_done)

    def get(self, session_id: UUID) -> Optional[InflightRun]:
        return self._runs.get(str(session_id))

    async def set_reason(self, session_id: UUID, *, reason: str) -> Optional[str]:
        async with self._lock:
            run = self._runs.get(str(session_id))
            if run is None:
                return None
            run.reason = reason
            return run.run_id

    async def cancel(self, session_id: UUID, *, reason: str = "user_interrupt") -> Optional[str]:
        """Cancel the inflight run for a session. Returns the run_id if cancelled."""
        async with self._lock:
            run = self._runs.get(str(session_id))
            if run is None or run.task.done():
                return None
            run.reason = reason
            run.task.cancel()
            log.info(
                "runtime.registry.cancel",
                session_id=str(session_id),
                run_id=run.run_id,
                reason=reason,
            )
            return run.run_id

    async def cancel_all(self) -> None:
        async with self._lock:
            for key, run in list(self._runs.items()):
                if not run.task.done():
                    run.task.cancel()
                    with contextlib.suppress(Exception):
                        await run.task
                self._runs.pop(key, None)


_registry = _Registry()


def get_registry() -> _Registry:
    return _registry
