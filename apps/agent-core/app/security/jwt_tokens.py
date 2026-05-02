from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt

from app.config import get_settings


def create_access_token(*, user_id: uuid.UUID, email: str, role: str = "user") -> str:
    s = get_settings()
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "email": email,
        "role": role if role in ("user", "admin") else "user",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=s.jwt_expire_hours)).timestamp()),
    }
    return jwt.encode(payload, s.jwt_secret, algorithm="HS256")


def decode_access_token(token: str) -> dict[str, Any]:
    s = get_settings()
    return jwt.decode(token, s.jwt_secret, algorithms=["HS256"])
