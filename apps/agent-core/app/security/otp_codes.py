"""Redis 中短时验证码与发送冷却。"""

from __future__ import annotations

import secrets
from typing import Literal

from app.storage.redis_client import get_redis

OtpKind = Literal["reg", "login"]

TTL_SEC = 600
COOLDOWN_SEC = 60


def _key_code(kind: OtpKind, email: str) -> str:
    return f"otp:{kind}:{email}"


def _key_cool(kind: OtpKind, email: str) -> str:
    return f"otp:cool:{kind}:{email}"


def generate_six_digit() -> str:
    return f"{secrets.randbelow(900_000) + 100_000:06d}"


async def check_cooldown(kind: OtpKind, email: str) -> bool:
    """若仍处于冷却期返回 True。"""
    r = get_redis()
    v = await r.get(_key_cool(kind, email))
    return v is not None


async def start_cooldown(kind: OtpKind, email: str) -> None:
    r = get_redis()
    await r.set(_key_cool(kind, email), "1", ex=COOLDOWN_SEC)


async def store_code(kind: OtpKind, email: str, code: str) -> None:
    r = get_redis()
    await r.set(_key_code(kind, email), code, ex=TTL_SEC)


async def verify_and_consume(kind: OtpKind, email: str, code: str) -> bool:
    r = get_redis()
    key = _key_code(kind, email)
    stored = await r.get(key)
    if stored is None or stored != code.strip():
        return False
    await r.delete(key)
    return True
