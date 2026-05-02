"""管理员：用户 CRUD（account_status / role）。"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import CurrentUser, normalize_email, require_admin
from app.logging_setup import get_logger
from app.security.passwords import hash_password
from app.storage.postgres import get_pool

log = get_logger(__name__)
router = APIRouter(prefix="/v1/admin/users", tags=["admin-users"])


class AdminUserRow(BaseModel):
    id: str
    email: str
    role: str
    account_status: str
    created_at: str | None = None
    has_password: bool
    has_google: bool


class AdminUserCreate(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=6, max_length=256)
    role: str = Field(default="user", pattern="^(user|admin)$")
    account_status: str = Field(default="active", pattern="^(active|disabled)$")


class AdminUserPatch(BaseModel):
    role: str | None = None
    account_status: str | None = None


def _normalize_role(value: object | None) -> str:
    r = str(value or "user")
    return r if r in ("user", "admin") else "user"


@router.get("", response_model=list[AdminUserRow])
async def list_users(_admin: CurrentUser = Depends(require_admin)) -> list[AdminUserRow]:
    del _admin
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, email, role, account_status, created_at,
                   (password_hash IS NOT NULL) AS has_password,
                   (google_sub IS NOT NULL) AS has_google
            FROM users
            ORDER BY created_at DESC
            """,
        )
    out: list[AdminUserRow] = []
    for row in rows:
        out.append(
            AdminUserRow(
                id=str(row["id"]),
                email=str(row["email"]),
                role=_normalize_role(row["role"]),
                account_status=str(row["account_status"] or "active"),
                created_at=row["created_at"].isoformat() if row.get("created_at") else None,
                has_password=bool(row["has_password"]),
                has_google=bool(row["has_google"]),
            ),
        )
    return out


@router.post("", response_model=AdminUserRow, status_code=201)
async def create_user(
    req: AdminUserCreate,
    admin: CurrentUser = Depends(require_admin),
) -> AdminUserRow:
    del admin
    email = normalize_email(req.email)
    pool = get_pool()
    async with pool.acquire() as conn:
        existing = await conn.fetchval("SELECT id FROM users WHERE email = $1", email)
        if existing:
            raise HTTPException(status_code=409, detail="email already registered")
        uid = uuid.uuid4()
        await conn.execute(
            """
            INSERT INTO users (id, email, password_hash, role, account_status)
            VALUES ($1, $2, $3, $4, $5)
            """,
            uid,
            email,
            hash_password(req.password),
            req.role,
            req.account_status,
        )
        row = await conn.fetchrow(
            """
            SELECT id, email, role, account_status, created_at,
                   (password_hash IS NOT NULL) AS has_password,
                   (google_sub IS NOT NULL) AS has_google
            FROM users WHERE id = $1
            """,
            uid,
        )
    assert row is not None
    log.info("admin.user_created", user_id=str(uid), email=email)
    return AdminUserRow(
        id=str(row["id"]),
        email=str(row["email"]),
        role=_normalize_role(row["role"]),
        account_status=str(row["account_status"] or "active"),
        created_at=row["created_at"].isoformat() if row.get("created_at") else None,
        has_password=bool(row["has_password"]),
        has_google=bool(row["has_google"]),
    )

@router.patch("/{user_id}", response_model=AdminUserRow)
async def patch_user(
    user_id: uuid.UUID,
    req: AdminUserPatch,
    admin: CurrentUser = Depends(require_admin),
) -> AdminUserRow:
    if req.role is None and req.account_status is None:
        raise HTTPException(status_code=400, detail="no fields to update")
    if req.role is not None and req.role not in ("user", "admin"):
        raise HTTPException(status_code=400, detail="invalid role")
    if req.account_status is not None and req.account_status not in ("active", "disabled"):
        raise HTTPException(status_code=400, detail="invalid account_status")
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, email, role, account_status FROM users WHERE id = $1",
            user_id,
        )
        if row is None:
            raise HTTPException(status_code=404, detail="user not found")
        next_role = req.role if req.role is not None else _normalize_role(row["role"])
        next_status = req.account_status if req.account_status is not None else str(row["account_status"] or "active")
        await conn.execute(
            "UPDATE users SET role = $2, account_status = $3, updated_at = now() WHERE id = $1",
            user_id,
            next_role,
            next_status,
        )
        row2 = await conn.fetchrow(
            """
            SELECT id, email, role, account_status, created_at,
                   (password_hash IS NOT NULL) AS has_password,
                   (google_sub IS NOT NULL) AS has_google
            FROM users WHERE id = $1
            """,
            user_id,
        )
    assert row2 is not None
    log.info("admin.user_patched", target_user_id=str(user_id), admin_id=str(admin.id))
    return AdminUserRow(
        id=str(row2["id"]),
        email=str(row2["email"]),
        role=_normalize_role(row2["role"]),
        account_status=str(row2["account_status"] or "active"),
        created_at=row2["created_at"].isoformat() if row2.get("created_at") else None,
        has_password=bool(row2["has_password"]),
        has_google=bool(row2["has_google"]),
    )


@router.delete("/{user_id}", status_code=204)
async def delete_user(
    user_id: uuid.UUID,
    admin: CurrentUser = Depends(require_admin),
) -> None:
    if user_id == admin.id:
        raise HTTPException(status_code=400, detail="cannot delete yourself")
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchval("SELECT id FROM users WHERE id = $1", user_id)
        if row is None:
            raise HTTPException(status_code=404, detail="user not found")
        async with conn.transaction():
            await conn.execute("DELETE FROM procedures WHERE user_id = $1", user_id)
            await conn.execute("DELETE FROM sandboxes WHERE user_id = $1", user_id)
            await conn.execute("DELETE FROM sessions WHERE user_id = $1", user_id)
            await conn.execute("DELETE FROM users WHERE id = $1", user_id)
    log.info("admin.user_deleted", target_user_id=str(user_id), admin_id=str(admin.id))
