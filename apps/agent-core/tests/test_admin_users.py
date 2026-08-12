"""Administrator user-management tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.api import admin_users
from app.api.deps import CurrentUser


class _Acquire:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _Pool:
    def __init__(self, conn):
        self._conn = conn

    def acquire(self):
        return _Acquire(self._conn)


@pytest.mark.asyncio
async def test_admin_can_reset_user_password_without_returning_it() -> None:
    target_id = uuid4()
    admin = CurrentUser(id=uuid4(), email="admin@example.com", role="admin")
    conn = AsyncMock()
    conn.fetchrow.return_value = {"email": "user@example.com"}

    with (
        patch.object(admin_users, "get_pool", return_value=_Pool(conn)),
        patch.object(admin_users, "hash_password", return_value="hashed-password") as hasher,
    ):
        result = await admin_users.reset_user_password(
            target_id,
            admin_users.AdminUserPasswordReset(new_password="new-secret"),
            admin,
        )

    assert result is None
    hasher.assert_called_once_with("new-secret")
    assert conn.fetchrow.await_args.args[1:] == (target_id, "hashed-password")


@pytest.mark.asyncio
async def test_reset_user_password_returns_not_found() -> None:
    target_id = uuid4()
    admin = CurrentUser(id=uuid4(), email="admin@example.com", role="admin")
    conn = AsyncMock()
    conn.fetchrow.return_value = None

    with (
        patch.object(admin_users, "get_pool", return_value=_Pool(conn)),
        patch.object(admin_users, "hash_password", return_value="hashed-password"),
        pytest.raises(HTTPException) as exc_info,
    ):
        await admin_users.reset_user_password(
            target_id,
            admin_users.AdminUserPasswordReset(new_password="new-secret"),
            admin,
        )

    assert exc_info.value.status_code == 404

