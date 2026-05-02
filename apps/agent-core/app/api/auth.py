"""用户注册、登录（邮箱密码 / 验证码 / Google）与 JWT。"""

from __future__ import annotations

import re
import uuid

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
from app.storage.postgres import get_pool

log = get_logger(__name__)
router = APIRouter(prefix="/v1/auth", tags=["auth"])


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
        await conn.execute(
            "INSERT INTO users (id, email, password_hash, role, account_status) VALUES ($1, $2, $3, $4, 'active')",
            uid,
            email,
            hash_password(req.password),
            role,
        )
    log.info("user.registered", user_id=str(uid), email=email, role=role)
    tok = create_access_token(user_id=uid, email=email, role=role)
    return TokenResp(
        access_token=tok,
        user=AuthUserOut(id=str(uid), email=email, role=role, account_status="active"),
    )


@router.post("/login", response_model=TokenResp)
async def login(req: LoginReq) -> TokenResp:
    email = normalize_email(req.email)
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, email, password_hash, google_sub, role, account_status FROM users WHERE email = $1",
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
        user=AuthUserOut(id=str(uid), email=email, role=rrole, account_status="active"),
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
            "SELECT id, email, role, account_status FROM users WHERE email = $1",
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
        user=AuthUserOut(id=str(uid), email=email, role=rrole, account_status="active"),
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
            "SELECT id, email, role, password_hash, google_sub, account_status FROM users WHERE google_sub = $1",
            sub,
        )
        row_email = await conn.fetchrow(
            "SELECT id, email, role, password_hash, google_sub, account_status FROM users WHERE email = $1",
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
            user=AuthUserOut(id=str(uid), email=em, role=rrole, account_status="active"),
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
            user=AuthUserOut(id=str(uid), email=email, role=rrole, account_status="active"),
        )
    role = _role_for_email(email)
    uid = uuid.uuid4()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO users (id, email, password_hash, google_sub, role, account_status) VALUES ($1, $2, NULL, $3, $4, 'active')",
            uid,
            email,
            sub,
            role,
        )
    log.info("user.google_registered", user_id=str(uid), email=email, role=role)
    tok = create_access_token(user_id=uid, email=email, role=role)
    return TokenResp(
        access_token=tok,
        user=AuthUserOut(id=str(uid), email=email, role=role, account_status="active"),
    )


@router.get("/me", response_model=AuthUserOut)
async def me(user: CurrentUser = Depends(get_current_user)) -> AuthUserOut:
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT id, email, role, account_status FROM users WHERE id = $1", user.id)
    if row is None:
        raise HTTPException(status_code=401, detail="user not found")
    rrole = _normalize_role(row["role"])
    ast = str(row["account_status"] or "active")
    return AuthUserOut(id=str(row["id"]), email=row["email"], role=rrole, account_status=ast)
