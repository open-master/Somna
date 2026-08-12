"""Plan points, point transactions, invite redemption, and admin management."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.api.deps import CurrentUser, get_current_user, require_admin
from app.logging_setup import get_logger
from app.services.billing import (
    default_billing_config,
    get_runtime_billing_config,
    normalize_billing_config,
    release_expired_billing_reservations,
    reset_runtime_billing_config,
    update_runtime_billing_config,
)
from app.services.points import (
    PLAN_NAMES,
    PLAN_ORDER,
    ensure_point_account,
    generate_invite_code,
    get_plan_catalog,
    invite_code_type_for_plan,
    invite_code_type_for_topup,
    invite_payload,
    normalize_invite_code,
    normalize_plan,
    plan_daily,
    plan_monthly,
)
from app.storage.postgres import get_pool

log = get_logger(__name__)
router = APIRouter(prefix="/v1/points", tags=["points"])

PlanId = Literal["basic", "pro"]
InviteAmount = Literal[1000, 2000, 3000, 4000, 5000]
InviteType = Literal[
    "sub_basic",
    "sub_pro",
    "topup_1000",
    "topup_2000",
    "topup_3000",
    "topup_4000",
    "topup_5000",
]
InviteStatus = Literal["all", "unused", "used"]
CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]
AdminDep = Annotated[CurrentUser, Depends(require_admin)]


class PlanPointAmounts(BaseModel):
    daily_points: int
    monthly_points: int


class PointAccountOverview(BaseModel):
    user_id: str
    email: str
    username: str
    has_password: bool
    role: str
    plan_type: str
    plan_expire_at: str | None
    daily_points: int
    monthly_points: int
    permanent_points: int
    total_points: int
    daily_reset_amount: int
    monthly_points_cap: int
    last_daily_reset_at: str | None
    plan_catalog: dict[str, PlanPointAmounts]


class PointTransactionRow(BaseModel):
    id: str
    amount: int
    total_after: int
    type: str
    description: str
    created_at: str


class PointTransactionList(BaseModel):
    items: list[PointTransactionRow]
    total: int


class UpgradePlanRequest(BaseModel):
    plan_id: PlanId
    invite_code: str = Field(min_length=3, max_length=64)


class PermanentTopupRequest(BaseModel):
    amount: InviteAmount
    invite_code: str = Field(min_length=3, max_length=64)


class InviteCodeCreateRequest(BaseModel):
    code_type: InviteType
    quantity: int = Field(default=1, ge=1, le=200)
    note: str | None = Field(default=None, max_length=200)


class InviteCodeRow(BaseModel):
    id: str
    code: str
    code_type: str
    plan_id: str | None
    topup_amount: int | None
    note: str | None
    consumed_by_user_id: str | None
    consumed_by_email: str | None
    consumed_at: str | None
    created_at: str


class InviteCodeList(BaseModel):
    items: list[InviteCodeRow]
    total: int


class AdminPointAccountRow(BaseModel):
    user_id: str
    email: str
    role: str
    account_status: str
    plan_type: str
    daily_points: int
    monthly_points: int
    permanent_points: int
    total_points: int


class AdminPointAccountList(BaseModel):
    items: list[AdminPointAccountRow]
    total: int


class AdminPointAdjustRequest(BaseModel):
    user_id: uuid.UUID
    amount: int = Field(ge=-1_000_000, le=1_000_000)
    description: str = Field(min_length=2, max_length=200)


class BillingCatalogResponse(BaseModel):
    config: dict[str, Any]
    defaults: dict[str, Any]


class BillingCatalogUpdateRequest(BaseModel):
    config: dict[str, Any]


def _iso(value: date | datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _total(row) -> int:
    return int(row["daily_points"]) + int(row["monthly_points"]) + int(row["permanent_points"])


def _account_overview(row, user: CurrentUser) -> PointAccountOverview:
    plan = normalize_plan(str(row["plan_type"]))
    return PointAccountOverview(
        user_id=str(user.id),
        email=user.email,
        username=user.username or user.email.split("@", 1)[0],
        has_password=user.has_password,
        role=user.role,
        plan_type=plan,
        plan_expire_at=_iso(row["plan_expire_at"]),
        daily_points=int(row["daily_points"]),
        monthly_points=int(row["monthly_points"]),
        permanent_points=int(row["permanent_points"]),
        total_points=_total(row),
        daily_reset_amount=plan_daily(plan),
        monthly_points_cap=plan_monthly(plan),
        last_daily_reset_at=_iso(row["last_daily_reset_at"]),
        plan_catalog={key: PlanPointAmounts(**values) for key, values in get_plan_catalog().items()},
    )


def _transaction_row(row) -> PointTransactionRow:
    return PointTransactionRow(
        id=str(row["id"]),
        amount=int(row["amount"]),
        total_after=int(row["total_after"]),
        type=str(row["type"]),
        description=str(row["description"]),
        created_at=_iso(row["created_at"]) or "",
    )


def _invite_row(row) -> InviteCodeRow:
    return InviteCodeRow(
        id=str(row["id"]),
        code=str(row["code"]),
        code_type=str(row["code_type"]),
        plan_id=str(row["plan_id"]) if row["plan_id"] is not None else None,
        topup_amount=int(row["topup_amount"]) if row["topup_amount"] is not None else None,
        note=str(row["note"]) if row["note"] is not None else None,
        consumed_by_user_id=(
            str(row["consumed_by_user_id"]) if row["consumed_by_user_id"] is not None else None
        ),
        consumed_by_email=(
            str(row["consumed_by_email"]) if row["consumed_by_email"] is not None else None
        ),
        consumed_at=_iso(row["consumed_at"]),
        created_at=_iso(row["created_at"]) or "",
    )


async def _lock_invite(conn, raw_code: str, expected_type: str):
    invite = await conn.fetchrow(
        """
        SELECT id, code_type, plan_id, topup_amount, consumed_at
        FROM invite_codes
        WHERE code = $1
        FOR UPDATE
        """,
        normalize_invite_code(raw_code),
    )
    if invite is None:
        raise HTTPException(status_code=400, detail="邀请码不存在")
    if invite["consumed_at"] is not None:
        raise HTTPException(status_code=400, detail="邀请码已被使用")
    if str(invite["code_type"]) != expected_type:
        raise HTTPException(status_code=400, detail="邀请码类型不匹配")
    return invite


async def _mark_invite_consumed(conn, invite_id, user: CurrentUser) -> None:
    await conn.execute(
        """
        UPDATE invite_codes
        SET consumed_by_user_id = $2, consumed_by_email = $3, consumed_at = now()
        WHERE id = $1
        """,
        invite_id,
        user.id,
        user.email,
    )


@router.get("/account", response_model=PointAccountOverview)
async def get_point_account(user: CurrentUserDep) -> PointAccountOverview:
    row = await release_expired_billing_reservations(user_id=user.id)
    return _account_overview(row, user)


@router.get("/billing-catalog", response_model=BillingCatalogResponse)
async def get_billing_catalog(_user: CurrentUserDep) -> BillingCatalogResponse:
    pool = get_pool()
    async with pool.acquire() as conn:
        config = await get_runtime_billing_config(conn)
    return BillingCatalogResponse(config=config, defaults=normalize_billing_config(default_billing_config()))


@router.get("/transactions", response_model=PointTransactionList)
async def list_point_transactions(
    user: CurrentUserDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> PointTransactionList:
    pool = get_pool()
    async with pool.acquire() as conn:
        total = await conn.fetchval(
            "SELECT count(*) FROM point_transactions WHERE user_id = $1",
            user.id,
        )
        rows = await conn.fetch(
            """
            SELECT id, amount, total_after, type, description, created_at
            FROM point_transactions
            WHERE user_id = $1
            ORDER BY created_at DESC, id DESC
            LIMIT $2 OFFSET $3
            """,
            user.id,
            limit,
            offset,
        )
    return PointTransactionList(items=[_transaction_row(row) for row in rows], total=int(total or 0))


@router.post("/upgrade", response_model=PointAccountOverview)
async def upgrade_plan(req: UpgradePlanRequest, user: CurrentUserDep) -> PointAccountOverview:
    expected_type = invite_code_type_for_plan(req.plan_id)
    pool = get_pool()
    async with pool.acquire() as conn, conn.transaction():
        invite = await _lock_invite(conn, req.invite_code, expected_type)
        account = await ensure_point_account(conn, user.id, for_update=True)
        current_plan = normalize_plan(str(account["plan_type"]))
        if PLAN_ORDER[req.plan_id] <= PLAN_ORDER[current_plan]:
            raise HTTPException(status_code=400, detail="仅支持从低等级升级到高等级")

        now = datetime.now(UTC)
        current_expiry = account["plan_expire_at"]
        expiry_base = current_expiry if current_expiry is not None and current_expiry > now else now
        old_total = _total(account)
        daily = plan_daily(req.plan_id)
        monthly = plan_monthly(req.plan_id)
        total_after = daily + monthly + int(account["permanent_points"])
        credited = total_after - old_total
        updated = await conn.fetchrow(
            """
            UPDATE point_accounts
            SET plan_type = $2, plan_expire_at = $3,
                daily_points = $4, monthly_points = $5,
                last_daily_reset_at = $6, last_monthly_reset_at = $7,
                updated_at = now()
            WHERE user_id = $1
            RETURNING *
            """,
            user.id,
            req.plan_id,
            expiry_base + timedelta(days=30),
            daily,
            monthly,
            now.date(),
            now.date().replace(day=1),
        )
        await _mark_invite_consumed(conn, invite["id"], user)
        await conn.execute(
            """
            INSERT INTO point_transactions
                (user_id, amount, total_after, type, description, invite_code_id)
            VALUES ($1, $2, $3, 'subscription', $4, $5)
            """,
            user.id,
            credited,
            total_after,
            f"升级 {PLAN_NAMES[req.plan_id]} 获得套餐积分",
            invite["id"],
        )
    if updated is None:
        raise RuntimeError("point account upgrade failed")
    log.info("points.plan_upgraded", user_id=str(user.id), plan=req.plan_id)
    return _account_overview(updated, user)


@router.post("/permanent-topup", response_model=PointAccountOverview)
async def topup_permanent_points(
    req: PermanentTopupRequest,
    user: CurrentUserDep,
) -> PointAccountOverview:
    expected_type = invite_code_type_for_topup(req.amount)
    pool = get_pool()
    async with pool.acquire() as conn, conn.transaction():
        invite = await _lock_invite(conn, req.invite_code, expected_type)
        account = await ensure_point_account(conn, user.id, for_update=True)
        total_after = _total(account) + req.amount
        updated = await conn.fetchrow(
            """
            UPDATE point_accounts
            SET permanent_points = permanent_points + $2, updated_at = now()
            WHERE user_id = $1
            RETURNING *
            """,
            user.id,
            req.amount,
        )
        await _mark_invite_consumed(conn, invite["id"], user)
        await conn.execute(
            """
            INSERT INTO point_transactions
                (user_id, amount, total_after, type, description, invite_code_id)
            VALUES ($1, $2, $3, 'permanent_topup', $4, $5)
            """,
            user.id,
            req.amount,
            total_after,
            f"充值永久积分 +{req.amount}",
            invite["id"],
        )
    if updated is None:
        raise RuntimeError("permanent point top-up failed")
    log.info("points.permanent_topped_up", user_id=str(user.id), amount=req.amount)
    return _account_overview(updated, user)


@router.get("/admin/accounts", response_model=AdminPointAccountList)
async def list_point_accounts(
    _admin: AdminDep,
    query: Annotated[str, Query(max_length=100)] = "",
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AdminPointAccountList:
    del _admin
    pattern = f"%{query.strip().lower()}%"
    pool = get_pool()
    async with pool.acquire() as conn:
        total = await conn.fetchval("SELECT count(*) FROM users WHERE lower(email) LIKE $1", pattern)
        rows = await conn.fetch(
            """
            SELECT u.id, u.email, u.role, u.account_status,
                   COALESCE(a.plan_type, 'free') AS plan_type,
                   COALESCE(a.daily_points, $2) AS daily_points,
                   COALESCE(a.monthly_points, $3) AS monthly_points,
                   COALESCE(a.permanent_points, 0) AS permanent_points
            FROM users u
            LEFT JOIN point_accounts a ON a.user_id = u.id
            WHERE lower(u.email) LIKE $1
            ORDER BY u.created_at DESC
            LIMIT $4 OFFSET $5
            """,
            pattern,
            plan_daily("free"),
            plan_monthly("free"),
            limit,
            offset,
        )
    return AdminPointAccountList(
        items=[
            AdminPointAccountRow(
                user_id=str(row["id"]),
                email=str(row["email"]),
                role=str(row["role"] or "user"),
                account_status=str(row["account_status"] or "active"),
                plan_type=normalize_plan(str(row["plan_type"])),
                daily_points=int(row["daily_points"]),
                monthly_points=int(row["monthly_points"]),
                permanent_points=int(row["permanent_points"]),
                total_points=(
                    int(row["daily_points"])
                    + int(row["monthly_points"])
                    + int(row["permanent_points"])
                ),
            )
            for row in rows
        ],
        total=int(total or 0),
    )


@router.post("/admin/adjust", response_model=AdminPointAccountRow)
async def adjust_point_account(
    req: AdminPointAdjustRequest,
    admin: AdminDep,
) -> AdminPointAccountRow:
    if req.amount == 0:
        raise HTTPException(status_code=400, detail="积分变更不能为 0")
    description = req.description.strip()
    if len(description) < 2:
        raise HTTPException(status_code=400, detail="请填写至少 2 个字符的调整原因")
    pool = get_pool()
    async with pool.acquire() as conn, conn.transaction():
        user_row = await conn.fetchrow(
            "SELECT id, email, role, account_status FROM users WHERE id = $1",
            req.user_id,
        )
        if user_row is None:
            raise HTTPException(status_code=404, detail="用户不存在")
        account = await ensure_point_account(conn, req.user_id, for_update=True)
        permanent_after = int(account["permanent_points"]) + req.amount
        if permanent_after < 0:
            raise HTTPException(status_code=400, detail="扣减后永久积分不能小于 0")
        total_after = int(account["daily_points"]) + int(account["monthly_points"]) + permanent_after
        updated = await conn.fetchrow(
            """
            UPDATE point_accounts
            SET permanent_points = $2, updated_at = now()
            WHERE user_id = $1
            RETURNING *
            """,
            req.user_id,
            permanent_after,
        )
        await conn.execute(
            """
            INSERT INTO point_transactions
                (user_id, amount, total_after, type, description, operator_user_id)
            VALUES ($1, $2, $3, 'admin_adjustment', $4, $5)
            """,
            req.user_id,
            req.amount,
            total_after,
            description,
            admin.id,
        )
    if updated is None:
        raise RuntimeError("point account adjustment failed")
    log.info(
        "points.admin_adjusted",
        admin_id=str(admin.id),
        target_user_id=str(req.user_id),
        amount=req.amount,
    )
    return AdminPointAccountRow(
        user_id=str(req.user_id),
        email=str(user_row["email"]),
        role=str(user_row["role"] or "user"),
        account_status=str(user_row["account_status"] or "active"),
        plan_type=normalize_plan(str(updated["plan_type"])),
        daily_points=int(updated["daily_points"]),
        monthly_points=int(updated["monthly_points"]),
        permanent_points=int(updated["permanent_points"]),
        total_points=_total(updated),
    )


@router.get("/admin/billing-catalog", response_model=BillingCatalogResponse)
async def admin_get_billing_catalog(_admin: AdminDep) -> BillingCatalogResponse:
    pool = get_pool()
    async with pool.acquire() as conn:
        config = await get_runtime_billing_config(conn)
    return BillingCatalogResponse(config=config, defaults=normalize_billing_config(default_billing_config()))


@router.patch("/admin/billing-catalog", response_model=BillingCatalogResponse)
async def admin_update_billing_catalog(
    req: BillingCatalogUpdateRequest,
    admin: AdminDep,
) -> BillingCatalogResponse:
    pool = get_pool()
    async with pool.acquire() as conn, conn.transaction():
        config = await update_runtime_billing_config(conn, req.config, updated_by_user_id=admin.id)
    log.info("points.billing_catalog_updated", admin_id=str(admin.id))
    return BillingCatalogResponse(config=config, defaults=normalize_billing_config(default_billing_config()))


@router.post("/admin/billing-catalog/reset", response_model=BillingCatalogResponse)
async def admin_reset_billing_catalog(admin: AdminDep) -> BillingCatalogResponse:
    pool = get_pool()
    async with pool.acquire() as conn, conn.transaction():
        config = await reset_runtime_billing_config(conn, updated_by_user_id=admin.id)
    log.info("points.billing_catalog_reset", admin_id=str(admin.id))
    return BillingCatalogResponse(config=config, defaults=normalize_billing_config(default_billing_config()))


@router.get("/admin/invite-codes", response_model=InviteCodeList)
async def list_invite_codes(
    _admin: AdminDep,
    code_type: Annotated[InviteType | None, Query()] = None,
    status: Annotated[InviteStatus, Query()] = "all",
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> InviteCodeList:
    del _admin
    pool = get_pool()
    async with pool.acquire() as conn:
        total = await conn.fetchval(
            """
            SELECT count(*) FROM invite_codes
            WHERE ($1::TEXT IS NULL OR code_type = $1)
              AND ($2::TEXT = 'all'
                   OR ($2 = 'used' AND consumed_at IS NOT NULL)
                   OR ($2 = 'unused' AND consumed_at IS NULL))
            """,
            code_type,
            status,
        )
        rows = await conn.fetch(
            """
            SELECT id, code, code_type, plan_id, topup_amount, note,
                   consumed_by_user_id, consumed_by_email, consumed_at, created_at
            FROM invite_codes
            WHERE ($1::TEXT IS NULL OR code_type = $1)
              AND ($2::TEXT = 'all'
                   OR ($2 = 'used' AND consumed_at IS NOT NULL)
                   OR ($2 = 'unused' AND consumed_at IS NULL))
            ORDER BY created_at DESC, id DESC
            LIMIT $3 OFFSET $4
            """,
            code_type,
            status,
            limit,
            offset,
        )
    return InviteCodeList(items=[_invite_row(row) for row in rows], total=int(total or 0))


@router.post("/admin/invite-codes", response_model=list[InviteCodeRow], status_code=201)
async def create_invite_codes(
    req: InviteCodeCreateRequest,
    admin: AdminDep,
) -> list[InviteCodeRow]:
    note = req.note.strip() if req.note and req.note.strip() else None
    plan_id, topup_amount = invite_payload(req.code_type)
    created = []
    pool = get_pool()
    async with pool.acquire() as conn, conn.transaction():
        for _ in range(req.quantity):
            row = None
            while row is None:
                row = await conn.fetchrow(
                    """
                    INSERT INTO invite_codes
                        (code, code_type, plan_id, topup_amount, note, created_by_user_id)
                    VALUES ($1, $2, $3, $4, $5, $6)
                    ON CONFLICT (code) DO NOTHING
                    RETURNING id, code, code_type, plan_id, topup_amount, note,
                              consumed_by_user_id, consumed_by_email, consumed_at, created_at
                    """,
                    generate_invite_code(),
                    req.code_type,
                    plan_id,
                    topup_amount,
                    note,
                    admin.id,
                )
            created.append(row)
    log.info("points.invite_codes_created", admin_id=str(admin.id), count=len(created))
    return [_invite_row(row) for row in created]
