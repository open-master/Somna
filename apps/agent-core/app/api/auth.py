"""用户注册、登录（邮箱密码 / 验证码 / Google）与 JWT。"""

from __future__ import annotations

import re
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token
from pydantic import BaseModel, Field

from app.api.deps import CurrentUser, get_current_user, normalize_email
from app.config import get_settings
from app.email.smtp_send import send_plain_email
from app.logging_setup import get_logger
from app.security.jwt_tokens import create_access_token
from app.security.otp_codes import (
    check_cooldown,
    generate_six_digit,
    start_cooldown,
    store_code,
    verify_and_consume,
)
from app.security.passwords import hash_password, verify_password
from app.services.account_profile import default_username, normalize_username
from app.storage.postgres import get_pool

log = get_logger(__name__)
router = APIRouter(prefix="/v1/auth", tags=["auth"])
UserDep = Annotated[CurrentUser, Depends(get_current_user)]


def _admin_set() -> set[str]:
    s = get_settings()
    raw = (s.admin_emails or "").strip()
    if not raw:
        return set()
    return {normalize_email(x) for x in raw.split(",") if x.strip()}


def _role_for_email(email: str) -> str:
    return "admin" if email in _admin_set() else "user"


def _validate_email_shape(email: str) -> None:
    if "@" not in email or not re.match(r"^[^@]+@[^@]+\.[^@]+$", email):
        raise HTTPException(status_code=400, detail="invalid email")


def _smtp_ready() -> bool:
    s = get_settings()
    return bool(s.smtp_host and s.smtp_user and s.smtp_password)


def _normalize_role(value: object | None) -> str:
    r = str(value or "user")
    return r if r in ("user", "admin") else "user"


def _reject_disabled(row: object | None) -> None:
    if row is None:
        return
    try:
        st = str(row["account_status"] or "active")  # type: ignore[index]
    except (KeyError, TypeError):
        st = "active"
    if st != "active":
        raise HTTPException(status_code=403, detail="account disabled")


class EmailOnlyReq(BaseModel):
    email: str = Field(min_length=3, max_length=255)


class RegisterReq(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=6, max_length=256)
    code: str = Field(min_length=4, max_length=12)


class LoginReq(BaseModel):
    email: str
    password: str


class LoginCodeReq(BaseModel):
    email: str
    code: str = Field(min_length=4, max_length=12)


class GoogleReq(BaseModel):
    credential: str = Field(min_length=10)


class AuthUserOut(BaseModel):
    id: str
    email: str
    role: str
    account_status: str = "active"
    username: str | None = None
    has_password: bool = False


class ProfileUpdateReq(BaseModel):
    username: str = Field(min_length=1, max_length=50)


class PasswordChangeReq(BaseModel):
    current_password: str | None = Field(default=None, max_length=256)
    new_password: str = Field(min_length=6, max_length=256)


class TokenResp(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: AuthUserOut


@router.post("/send-registration-code", status_code=204)
async def send_registration_code(req: EmailOnlyReq) -> None:
    email = normalize_email(req.email)
    _validate_email_shape(email)
    if not _smtp_ready():
        raise HTTPException(status_code=503, detail="email service not configured")
    pool = get_pool()
    async with pool.acquire() as conn:
        existing = await conn.fetchval("SELECT id FROM users WHERE email = $1", email)
    if existing:
        raise HTTPException(status_code=409, detail="email already registered")
    if await check_cooldown("reg", email):
        raise HTTPException(status_code=429, detail="please wait before requesting another code")
    code = generate_six_digit()
    await store_code("reg", email, code)
    await start_cooldown("reg", email)
    try:
        await send_plain_email(
            to=email,
            subject="Somna AI 注册验证码",
            body=f"您的注册验证码为 {code}，10 分钟内有效。如非本人操作请忽略。",
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("smtp.reg_code_failed", email=email, error=str(exc))
        raise HTTPException(status_code=502, detail="failed to send email") from exc


@router.post("/send-login-code", status_code=204)
async def send_login_code(req: EmailOnlyReq) -> None:
    email = normalize_email(req.email)
    _validate_email_shape(email)
    if not _smtp_ready():
        raise HTTPException(status_code=503, detail="email service not configured")
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT id, account_status FROM users WHERE email = $1", email)
    if row is None:
        return
    _reject_disabled(row)
    if await check_cooldown("login", email):
        raise HTTPException(status_code=429, detail="please wait before requesting another code")
    code = generate_six_digit()
    await store_code("login", email, code)
    await start_cooldown("login", email)
    try:
        await send_plain_email(
            to=email,
            subject="Somna AI 登录验证码",
            body=f"您的登录验证码为 {code}，10 分钟内有效。如非本人操作请忽略。",
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("smtp.login_code_failed", email=email, error=str(exc))
        raise HTTPException(status_code=502, detail="failed to send email") from exc


@router.post("/register", response_model=TokenResp, status_code=201)
async def register(req: RegisterReq) -> TokenResp:
    email = normalize_email(req.email)
    _validate_email_shape(email)
    if not await verify_and_consume("reg", email, req.code):
        raise HTTPException(status_code=400, detail="invalid or expired verification code")
    role = _role_for_email(email)
    pool = get_pool()
    async with pool.acquire() as conn:
        existing = await conn.fetchval("SELECT id FROM users WHERE email = $1", email)
        if existing:
            raise HTTPException(status_code=409, detail="email already registered")
        uid = uuid.uuid4()
        username = default_username(email)
        await conn.execute(
            """
            INSERT INTO users (id, email, username, password_hash, role, account_status)
            VALUES ($1, $2, $3, $4, $5, 'active')
            """,
            uid,
            email,
            username,
            hash_password(req.password),
            role,
        )
    log.info("user.registered", user_id=str(uid), email=email, role=role)
    tok = create_access_token(user_id=uid, email=email, role=role)
    return TokenResp(
        access_token=tok,
        user=AuthUserOut(
            id=str(uid),
            email=email,
            role=role,
            account_status="active",
            username=username,
            has_password=True,
        ),
    )


@router.post("/login", response_model=TokenResp)
async def login(req: LoginReq) -> TokenResp:
    email = normalize_email(req.email)
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, email, username, password_hash, google_sub, role, account_status
            FROM users WHERE email = $1
            """,
            email,
        )
    if row is None:
        raise HTTPException(status_code=401, detail="invalid email or password")
    _reject_disabled(row)
    ph = row["password_hash"]
    if not ph:
        raise HTTPException(
            status_code=400,
            detail="this account uses another sign-in method; use Google or set a password",
        )
    if not verify_password(req.password, ph):
        raise HTTPException(status_code=401, detail="invalid email or password")
    uid = row["id"]
    rrole = _normalize_role(row["role"])
    tok = create_access_token(user_id=uid, email=email, role=rrole)
    return TokenResp(
        access_token=tok,
        user=AuthUserOut(
            id=str(uid),
            email=email,
            role=rrole,
            account_status="active",
            username=str(row["username"] or "").strip() or default_username(email),
            has_password=True,
        ),
    )


@router.post("/login-code", response_model=TokenResp)
async def login_code(req: LoginCodeReq) -> TokenResp:
    email = normalize_email(req.email)
    _validate_email_shape(email)
    if not await verify_and_consume("login", email, req.code):
        raise HTTPException(status_code=401, detail="invalid or expired verification code")
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, email, username, password_hash, role, account_status FROM users WHERE email = $1",
            email,
        )
    if row is None:
        raise HTTPException(status_code=401, detail="invalid or expired verification code")
    _reject_disabled(row)
    uid = row["id"]
    rrole = _normalize_role(row["role"])
    tok = create_access_token(user_id=uid, email=email, role=rrole)
    return TokenResp(
        access_token=tok,
        user=AuthUserOut(
            id=str(uid),
            email=email,
            role=rrole,
            account_status="active",
            username=str(row["username"] or "").strip() or default_username(email),
            has_password=bool(row["password_hash"]),
        ),
    )


@router.post("/google", response_model=TokenResp)
async def google_auth(req: GoogleReq) -> TokenResp:
    s = get_settings()
    cid = (s.google_oauth_client_id or "").strip()
    if not cid:
        raise HTTPException(status_code=503, detail="google sign-in not configured")
    try:
        info = google_id_token.verify_oauth2_token(
            req.credential,
            google_requests.Request(),
            audience=cid,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("google.verify_failed", error=str(exc))
        raise HTTPException(status_code=401, detail="invalid google credential") from exc
    if not info.get("email_verified"):
        raise HTTPException(status_code=400, detail="google email not verified")
    sub = str(info.get("sub") or "")
    email_raw = str(info.get("email") or "").strip().lower()
    if not sub or "@" not in email_raw:
        raise HTTPException(status_code=400, detail="google token missing identity")
    email = normalize_email(email_raw)
    _validate_email_shape(email)
    pool = get_pool()
    async with pool.acquire() as conn:
        row_sub = await conn.fetchrow(
            """
            SELECT id, email, username, role, password_hash, google_sub, account_status
            FROM users WHERE google_sub = $1
            """,
            sub,
        )
        row_email = await conn.fetchrow(
            """
            SELECT id, email, username, role, password_hash, google_sub, account_status
            FROM users WHERE email = $1
            """,
            email,
        )
    if row_sub:
        _reject_disabled(row_sub)
        uid = row_sub["id"]
        rrole = _normalize_role(row_sub["role"])
        em = normalize_email(str(row_sub["email"]))
        tok = create_access_token(user_id=uid, email=em, role=rrole)
        return TokenResp(
            access_token=tok,
            user=AuthUserOut(
                id=str(uid),
                email=em,
                role=rrole,
                account_status="active",
                username=str(row_sub["username"] or "").strip() or default_username(em),
                has_password=bool(row_sub["password_hash"]),
            ),
        )
    if row_email:
        _reject_disabled(row_email)
        g = row_email["google_sub"]
        ph = row_email["password_hash"]
        if g and g != sub:
            raise HTTPException(status_code=409, detail="account conflict")
        if g is None and ph:
            raise HTTPException(
                status_code=409,
                detail="email already registered with password; sign in with password first",
            )
        uid = row_email["id"]
        rrole = _normalize_role(row_email["role"])
        async with pool.acquire() as conn:
            await conn.execute("UPDATE users SET google_sub = $2, updated_at = now() WHERE id = $1", uid, sub)
        tok = create_access_token(user_id=uid, email=email, role=rrole)
        return TokenResp(
            access_token=tok,
            user=AuthUserOut(
                id=str(uid),
                email=email,
                role=rrole,
                account_status="active",
                username=str(row_email["username"] or "").strip() or default_username(email),
                has_password=bool(row_email["password_hash"]),
            ),
        )
    role = _role_for_email(email)
    uid = uuid.uuid4()
    username = default_username(email)
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO users (id, email, username, password_hash, google_sub, role, account_status)
            VALUES ($1, $2, $3, NULL, $4, $5, 'active')
            """,
            uid,
            email,
            username,
            sub,
            role,
        )
    log.info("user.google_registered", user_id=str(uid), email=email, role=role)
    tok = create_access_token(user_id=uid, email=email, role=role)
    return TokenResp(
        access_token=tok,
        user=AuthUserOut(
            id=str(uid),
            email=email,
            role=role,
            account_status="active",
            username=username,
            has_password=False,
        ),
    )


@router.get("/me", response_model=AuthUserOut)
async def me(user: UserDep) -> AuthUserOut:
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, email, username, password_hash, role, account_status FROM users WHERE id = $1",
            user.id,
        )
    if row is None:
        raise HTTPException(status_code=401, detail="user not found")
    rrole = _normalize_role(row["role"])
    ast = str(row["account_status"] or "active")
    return AuthUserOut(
        id=str(row["id"]),
        email=row["email"],
        role=rrole,
        account_status=ast,
        username=str(row["username"] or "").strip() or default_username(str(row["email"])),
        has_password=bool(row["password_hash"]),
    )


@router.patch("/me", response_model=AuthUserOut)
async def update_me(req: ProfileUpdateReq, user: UserDep) -> AuthUserOut:
    try:
        username = normalize_username(req.username)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE users
            SET username = $2, updated_at = now()
            WHERE id = $1
            RETURNING id, email, username, password_hash, role, account_status
            """,
            user.id,
            username,
        )
    if row is None:
        raise HTTPException(status_code=404, detail="user not found")
    log.info("user.profile_updated", user_id=str(user.id))
    return AuthUserOut(
        id=str(row["id"]),
        email=str(row["email"]),
        role=_normalize_role(row["role"]),
        account_status=str(row["account_status"] or "active"),
        username=str(row["username"]),
        has_password=bool(row["password_hash"]),
    )


@router.put("/me/password", status_code=204)
async def change_my_password(req: PasswordChangeReq, user: UserDep) -> None:
    if len(req.new_password.encode("utf-8")) > 72:
        raise HTTPException(status_code=400, detail="新密码不能超过 72 个字节")
    pool = get_pool()
    async with pool.acquire() as conn, conn.transaction():
        row = await conn.fetchrow(
            "SELECT password_hash FROM users WHERE id = $1 FOR UPDATE",
            user.id,
        )
        if row is None:
            raise HTTPException(status_code=404, detail="user not found")
        current_hash = row["password_hash"]
        if current_hash:
            if not req.current_password:
                raise HTTPException(status_code=400, detail="请输入当前密码")
            if not verify_password(req.current_password, str(current_hash)):
                raise HTTPException(status_code=400, detail="当前密码不正确")
            if verify_password(req.new_password, str(current_hash)):
                raise HTTPException(status_code=400, detail="新密码不能与当前密码相同")
        await conn.execute(
            "UPDATE users SET password_hash = $2, updated_at = now() WHERE id = $1",
            user.id,
            hash_password(req.new_password),
        )
    log.info("user.password_changed", user_id=str(user.id), password_was_set=bool(current_hash))
