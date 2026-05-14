"""FastAPI entrypoint for Somna Agent Core."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.auth import router as auth_router
from app.api.admin_users import router as admin_users_router
from app.api.health import router as health_router
from app.api.sessions import router as sessions_router
from app.api.skills import router as skills_router
from app.api.stream import router as stream_router
from app.api.temporal import router as temporal_router
from app.config import get_settings
from app.graph.runtime import get_registry
from app.graph.session_graph import close_graph, get_compiled_graph
from app.logging_setup import get_logger, setup_logging
from app.services.skills import ensure_skill_tables, seed_builtin_skills
from app.storage.nats_client import close_nats, init_nats
from app.storage.postgres import close_pool, init_pool
from app.storage.redis_client import close_redis, init_redis
from app.temporal.client import close_temporal_client
from app.temporal.worker import start_temporal_worker, stop_temporal_worker
from app.tools.client import close_client
from app.tools.schema import refresh_cache


async def _wait_for_litellm(*, log, max_wait_s: float = 180.0, interval_s: float = 2.0) -> None:
    """Block until LiteLLM answers liveliness, so Temporal activities don't hit a dead gateway."""
    settings = get_settings()
    url = f"{settings.litellm_url.rstrip('/')}/health/liveliness"
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max_wait_s
    while loop.time() < deadline:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(url)
                if resp.status_code == 200:
                    log.info("litellm.ready", url=url)
                    return
        except Exception as exc:  # noqa: BLE001
            log.debug("litellm.wait", url=url, error=str(exc))
        await asyncio.sleep(interval_s)
    log.warning("litellm.wait_timeout", url=url, max_wait_s=max_wait_s)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    setup_logging(settings.log_level)
    log = get_logger(__name__)
    log.info("agent-core.start", env=settings.env, service=settings.service_name)

    await init_pool()
    await ensure_skill_tables()
    await seed_builtin_skills()
    await init_redis()
    await init_nats()
    # Warm up the graph so the first request doesn't pay the setup cost
    try:
        await get_compiled_graph()
    except Exception as exc:  # noqa: BLE001
        log.warning("agent-core.graph.warmup_failed", error=str(exc))
    # Warm up tool manifest cache (non-fatal if mcp-hub is slow / absent)
    try:
        await refresh_cache()
    except Exception as exc:  # noqa: BLE001
        log.warning("agent-core.tools.warmup_failed", error=str(exc))
    await _wait_for_litellm(log=log)
    try:
        await start_temporal_worker()
    except Exception as exc:  # noqa: BLE001
        log.warning("agent-core.temporal.worker_start_failed", error=str(exc))

    log.info("agent-core.ready", port=8000)
    try:
        yield
    finally:
        log.info("agent-core.stop")
        await get_registry().cancel_all()
        await stop_temporal_worker()
        await close_temporal_client()
        await close_graph()
        await close_nats()
        await close_redis()
        await close_pool()
        await close_client()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Somna Agent Core",
        version="0.1.0",
        description="LangGraph + Claude Agent SDK + mem0 + LiteLLM",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # dev only; tightened at BFF in prod
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(admin_users_router)
    app.include_router(sessions_router)
    app.include_router(skills_router)
    app.include_router(stream_router)
    app.include_router(temporal_router)

    return app


app = create_app()
