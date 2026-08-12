"""Authenticated profile and password-management tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.api import auth
from app.api.deps import CurrentUser
from app.services.account_profile import default_username, normalize_username


class _Context:
    def __init__(self, value):
        self._value = value

    async def __aenter__(self):
        return self._value

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _Conn:
    def __init__(self, rows):
        self._rows = list(rows)
        self.execute = AsyncMock()

    async def fetchrow(self, *_args, **_kwargs):
        return self._rows.pop(0) if self._rows else None

    def transaction(self):
        return _Context(self)


class _Pool:
    def __init__(self, conn):
        self._conn = conn

    def acquire(self):
        return _Context(self._conn)


def _user(*, has_password: bool = True) -> CurrentUser:
    return CurrentUser(
        id=uuid4(),
        email="old-name@example.com",
        role="user",
        username="old-name",
        has_password=has_password,
    )


def test_username_helpers_preserve_display_names() -> None:
    assert default_username("  hello@example.com ") == "hello"
    assert normalize_username("  星河用户  ") == "星河用户"
    with pytest.raises(ValueError):
        normalize_username("   ")


@pytest.mark.asyncio
async def test_user_can_update_own_username() -> None:
    user = _user()
    conn = _Conn(
        [
            {
                "id": user.id,
                "email": user.email,
                "username": "新用户名",
                "password_hash": "hash",
                "role": "user",
                "account_status": "active",
            }
        ]
    )
    with patch.object(auth, "get_pool", return_value=_Pool(conn)):
        result = await auth.update_me(auth.ProfileUpdateReq(username="  新用户名  "), user)

    assert result.username == "新用户名"
    assert result.has_password is True


@pytest.mark.asyncio
async def test_existing_password_requires_current_password() -> None:
    user = _user()
    conn = _Conn([{"password_hash": "old-hash"}])
    with (
        patch.object(auth, "get_pool", return_value=_Pool(conn)),
        pytest.raises(HTTPException) as exc_info,
    ):
        await auth.change_my_password(
            auth.PasswordChangeReq(new_password="new-secret"),
            user,
        )

    assert exc_info.value.detail == "请输入当前密码"
    conn.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_google_only_user_can_set_first_password() -> None:
    user = _user(has_password=False)
    conn = _Conn([{"password_hash": None}])
    with (
        patch.object(auth, "get_pool", return_value=_Pool(conn)),
        patch.object(auth, "hash_password", return_value="new-hash") as hasher,
    ):
        await auth.change_my_password(
            auth.PasswordChangeReq(new_password="new-secret"),
            user,
        )

    hasher.assert_called_once_with("new-secret")
    assert conn.execute.await_args.args[1:] == (user.id, "new-hash")

