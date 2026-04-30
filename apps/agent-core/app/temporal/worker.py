from __future__ import annotations

import asyncio

from temporalio.worker import Worker

from app.config import get_settings
from app.logging_setup import get_logger
from app.temporal.activities import run_session_graph_activity
from app.temporal.client import get_temporal_client
from app.temporal.workflows import SessionRunWorkflow

log = get_logger(__name__)

_worker: Worker | None = None
_worker_task: asyncio.Task | None = None


async def start_temporal_worker() -> None:
    global _worker, _worker_task
    if _worker_task is not None:
        return
    client = await get_temporal_client()
    _worker = Worker(
        client,
        task_queue=get_settings().temporal_task_queue,
        workflows=[SessionRunWorkflow],
        activities=[run_session_graph_activity],
    )
    _worker_task = asyncio.create_task(_worker.run())
    log.info("temporal.worker.started", task_queue=get_settings().temporal_task_queue)


async def stop_temporal_worker() -> None:
    global _worker, _worker_task
    if _worker is not None:
        await _worker.shutdown()
    if _worker_task is not None:
        await _worker_task
    _worker = None
    _worker_task = None
