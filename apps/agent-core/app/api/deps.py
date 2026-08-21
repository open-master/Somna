"""FastAPI dependencies: authentication."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.security.jwt_tokens import decode_access_token
from app.storage.postgres import get_pool

security = HTTPBearer(auto_error=False)

# 与前端 middleware、lib/auth/cookie.ts 一致；预览 <img> 只带 Cookie
_ACCESS_TOKEN_COOKIE = "somna_access_token"


def normalize_email(value: str) -> str:
    return (value or "").strip().lower()


@dataclass(frozen=True)
class CurrentUser:
    id: uuid.UUID
    email: str
    role: str = "user"
    username: str | None = None
    has_password: bool = False


def _normalize_role(value: object | None) -> str:
    r = str(value or "user")
    return r if r in ("user", "admin") else "user"


async def get_current_user(
    request: Request,
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(security)],
) -> CurrentUser:
    token: str | None = None
    if creds and creds.scheme.lower() == "bearer":
        token = creds.credentials
    if not token:
        token = request.cookies.get(_ACCESS_TOKEN_COOKIE)
    if not token:
        raise HTTPException(status_code=401, detail="not authenticated")
    try:
        payload = decode_access_token(token)
        uid = uuid.UUID(str(payload["sub"]))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=401, detail="invalid or expired token") from exc
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT email, username, password_hash, role, account_status FROM users WHERE id = $1",
            uid,
        )
    if row is None:
        raise HTTPException(status_code=401, detail="user not found")
    st = str(row.get("account_status") or "active")
    if st != "active":
        raise HTTPException(status_code=403, detail="account disabled")
    return CurrentUser(
        id=uid,
        email=normalize_email(str(row["email"])),
        role=_normalize_role(row["role"]),
        username=str(row.get("username") or "").strip() or None,
        has_password=bool(row.get("password_hash")),
    )


async def require_admin(user: Annotated[CurrentUser, Depends(get_current_user)]) -> CurrentUser:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="admin only")
    return user
