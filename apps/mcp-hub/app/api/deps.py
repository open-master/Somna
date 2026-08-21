"""Internal service-to-service auth for MCP Hub."""

from __future__ import annotations

import hmac
from typing import Annotated

from fastapi import Header, HTTPException, status

from app.config import get_settings

_AUTH_SCHEME = "Bearer "


def _tokens_match(provided: str, expected: str) -> bool:
    try:
        return hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))
    except (TypeError, ValueError):
        return False


def require_internal_token(
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    expected = (get_settings().mcp_internal_token or "").strip()
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="MCP_HUB_INTERNAL_TOKEN is not configured",
        )
    if not authorization or not authorization.startswith(_AUTH_SCHEME):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="unauthorized")
    provided = authorization[len(_AUTH_SCHEME) :].strip()
    if not provided or not _tokens_match(provided, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="unauthorized")
