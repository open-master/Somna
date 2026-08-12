"""Account-profile schema compatibility and display-name helpers."""

from __future__ import annotations

from app.storage.postgres import get_pool


def default_username(email: str) -> str:
    local = (email or "").strip().split("@", 1)[0].strip()
    return local[:50] or "用户"


def normalize_username(value: str) -> str:
    username = (value or "").strip()
    if not username:
        raise ValueError("用户名不能为空")
    if len(username) > 50:
        raise ValueError("用户名不能超过 50 个字符")
    if any(ord(char) < 32 or ord(char) == 127 for char in username):
        raise ValueError("用户名不能包含控制字符")
    return username


async def ensure_account_profile_columns() -> None:
    """Upgrade databases created before persistent usernames were introduced."""
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS username TEXT")
        await conn.execute(
            """
            UPDATE users
            SET username = left(split_part(email, '@', 1), 50), updated_at = now()
            WHERE username IS NULL OR btrim(username) = ''
            """
        )

