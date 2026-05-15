from __future__ import annotations

import asyncio
from contextlib import suppress
from uuid import UUID

from temporalio import activity

from app.graph.runner import run_session_graph
from app.temporal.models import SessionWorkflowInput

_HEARTBEAT_INTERVAL_SECONDS = 10


async def _heartbeat_until_done(done: asyncio.Event) -> None:
    while not done.is_set():
        activity.heartbeat()
        try:
            await asyncio.wait_for(done.wait(), timeout=_HEARTBEAT_INTERVAL_SECONDS)
        except asyncio.TimeoutError:
            continue


@activity.defn
async def run_session_graph_activity(req: SessionWorkflowInput) -> None:
    done = asyncio.Event()
    task = asyncio.create_task(
        run_session_graph(
            session_id=UUID(req.session_id),
            run_id=req.run_id,
            user_id=req.user_id,
            text=req.text,
            planner_model=req.planner_model,
            executor_model=req.executor_model,
            executor_engine=req.executor_engine,
            task_frame_model=req.task_frame_model,
            compact_model=req.compact_model,
            coder_model=req.coder_model,
            reasoner_model=req.reasoner_model,
            longctx_model=req.longctx_model,
            skill_model=req.skill_model,
            skill_mode=req.skill_mode,
            mcp_tool_models=req.mcp_tool_models,
            attachments=list(req.attachments or []),
        )
    )
    heartbeat_task = asyncio.create_task(_heartbeat_until_done(done))
    try:
        await task
    finally:
        done.set()
        heartbeat_task.cancel()
        with suppress(asyncio.CancelledError):
            await heartbeat_task
        if not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
