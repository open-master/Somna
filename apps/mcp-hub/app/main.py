"""MCP Hub entrypoint."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI

# Register built-in tools (side-effect import).
import app.tools  # noqa: F401
from app import __version__
from app.api import health, sandbox, tools
from app.api.deps import require_internal_token
from app.config import get_settings
from app.logging_setup import get_logger, setup_logging
from app.sandbox.manager import get_sandbox_manager
from app.tools import registry as tool_registry


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    setup_logging(settings.log_level)
    log = get_logger("mcp-hub")

    sm = get_sandbox_manager(settings)
    tools_list = [t.name for t in tool_registry.all_tools()]
    log.info(
        "mcp_hub.startup",
        version=__version__,
        sandbox_root=settings.sandbox_root,
        existing_sandboxes=len(sm.list_all()),
        tools=tools_list,
        search_provider=settings.search_provider,
        auth_configured=bool((settings.mcp_internal_token or "").strip()),
    )
    yield
    log.info("mcp_hub.shutdown")


app = FastAPI(
    title="Somna MCP Hub",
    version=__version__,
    description="Tool server exposing shell / filesystem / search to Agent Core.",
    lifespan=lifespan,
)

app.include_router(health.router)
app.include_router(tools.router, dependencies=[Depends(require_internal_token)])
app.include_router(sandbox.router, dependencies=[Depends(require_internal_token)])
