"""Plan point-account schema and invite-code helpers."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime

from app.config import get_settings

PLAN_NAMES = {"free": "免费版", "basic": "基础版", "pro": "专业版"}
PLAN_ORDER = {"free": 0, "basic": 1, "pro": 2}
TOPUP_AMOUNTS = (1000, 2000, 3000, 4000, 5000)
INVITE_TYPES: dict[str, tuple[str | None, int | None]] = {
    "sub_basic": ("basic", None),
    "sub_pro": ("pro", None),
    **{f"topup_{amount}": (None, amount) for amount in TOPUP_AMOUNTS},
}
_INVITE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def get_plan_catalog() -> dict[str, dict[str, int]]:
    settings = get_settings()
    return {
        "free": {
            "daily_points": max(0, int(settings.plan_free_daily)),
            "monthly_points": max(0, int(settings.plan_free_monthly)),
        },
        "basic": {
            "daily_points": max(0, int(settings.plan_basic_daily)),
            "monthly_points": max(0, int(settings.plan_basic_monthly)),
        },
        "pro": {
            "daily_points": max(0, int(settings.plan_pro_daily)),
            "monthly_points": max(0, int(settings.plan_pro_monthly)),
        },
    }


# Backwards-compatible snapshot for callers that expose the catalog directly.
# Per-plan lookup helpers below read Settings on every call.
PLAN_CATALOG: dict[str, dict[str, int]] = get_plan_catalog()


def normalize_plan(value: str | None) -> str:
    return value if value in PLAN_ORDER else "free"


def plan_daily(value: str | None) -> int:
    return get_plan_catalog()[normalize_plan(value)]["daily_points"]


def plan_monthly(value: str | None) -> int:
    return get_plan_catalog()[normalize_plan(value)]["monthly_points"]


def invite_code_type_for_topup(amount: int) -> str:
    if amount not in TOPUP_AMOUNTS:
        raise ValueError("unsupported invite amount")
    return f"topup_{amount}"


def invite_code_type_for_plan(plan: str) -> str:
    normalized = normalize_plan(plan)
    if normalized not in {"basic", "pro"}:
        raise ValueError("unsupported invite plan")
    return f"sub_{normalized}"


def invite_payload(code_type: str) -> tuple[str | None, int | None]:
    try:
        return INVITE_TYPES[code_type]
    except KeyError as exc:
        raise ValueError("unsupported invite type") from exc


def normalize_invite_code(value: str) -> str:
    return (value or "").strip().upper()


def generate_invite_code() -> str:
    left = "".join(secrets.choice(_INVITE_ALPHABET) for _ in range(5))
    right = "".join(secrets.choice(_INVITE_ALPHABET) for _ in range(5))
    return f"SM-{left}-{right}"


async def ensure_point_account(conn, user_id, *, for_update: bool = False):
    """Create, reset, and return a user's point account inside the caller transaction."""
    today = datetime.now(UTC).date()
    month_start = today.replace(day=1)
    await conn.execute(
        """
        INSERT INTO point_accounts (
            user_id, plan_type, daily_points, monthly_points,
            last_daily_reset_at, last_monthly_reset_at
        )
        VALUES ($1, 'free', $2, $3, $4, $5)
        ON CONFLICT (user_id) DO NOTHING
        """,
        user_id,
        plan_daily("free"),
        plan_monthly("free"),
        today,
        month_start,
    )
    suffix = " FOR UPDATE" if for_update else ""
    row = await conn.fetchrow(f"SELECT * FROM point_accounts WHERE user_id = $1{suffix}", user_id)
    if row is None:
        raise RuntimeError("point account could not be created")

    plan = normalize_plan(str(row["plan_type"]))
    plan_expire_at = row["plan_expire_at"]
    expired = plan != "free" and plan_expire_at is not None and plan_expire_at <= datetime.now(UTC)
    daily_due = row["last_daily_reset_at"] != today
    monthly_due = row["last_monthly_reset_at"] is None or row["last_monthly_reset_at"] < month_start
    if expired:
        plan = "free"
        daily_due = True
        monthly_due = True

    if expired or daily_due or monthly_due:
        row = await conn.fetchrow(
            """
            UPDATE point_accounts
            SET plan_type = $2,
                plan_expire_at = CASE WHEN $3::BOOLEAN THEN NULL ELSE plan_expire_at END,
                daily_points = CASE WHEN $4::BOOLEAN THEN $5 ELSE daily_points END,
                monthly_points = CASE WHEN $6::BOOLEAN THEN $7 ELSE monthly_points END,
                last_daily_reset_at = CASE WHEN $4::BOOLEAN THEN $8 ELSE last_daily_reset_at END,
                last_monthly_reset_at = CASE WHEN $6::BOOLEAN THEN $9 ELSE last_monthly_reset_at END,
                updated_at = now()
            WHERE user_id = $1
            RETURNING *
            """,
            user_id,
            plan,
            expired,
            daily_due,
            plan_daily(plan),
            monthly_due,
            plan_monthly(plan),
            today,
            month_start,
        )
        if row is None:
            raise RuntimeError("point account reset failed")
    return row


async def ensure_point_tables() -> None:
    """Create or upgrade point tables for databases whose init scripts already ran."""
    from app.storage.postgres import get_pool

    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS point_accounts (
                user_id               UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                plan_type             TEXT NOT NULL DEFAULT 'free',
                plan_expire_at        TIMESTAMPTZ,
                daily_points          BIGINT NOT NULL DEFAULT 10,
                monthly_points        BIGINT NOT NULL DEFAULT 50,
                permanent_points      BIGINT NOT NULL DEFAULT 0,
                last_daily_reset_at   DATE,
                last_monthly_reset_at DATE,
                created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at            TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        for ddl in (
            "ALTER TABLE point_accounts ADD COLUMN IF NOT EXISTS plan_type TEXT NOT NULL DEFAULT 'free'",
            "ALTER TABLE point_accounts ADD COLUMN IF NOT EXISTS plan_expire_at TIMESTAMPTZ",
            "ALTER TABLE point_accounts ADD COLUMN IF NOT EXISTS daily_points BIGINT NOT NULL DEFAULT 10",
            "ALTER TABLE point_accounts ADD COLUMN IF NOT EXISTS monthly_points BIGINT NOT NULL DEFAULT 50",
            "ALTER TABLE point_accounts ADD COLUMN IF NOT EXISTS permanent_points BIGINT NOT NULL DEFAULT 0",
            "ALTER TABLE point_accounts ADD COLUMN IF NOT EXISTS last_daily_reset_at DATE",
            "ALTER TABLE point_accounts ADD COLUMN IF NOT EXISTS last_monthly_reset_at DATE",
        ):
            await conn.execute(ddl)
        columns = await conn.fetch(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'point_accounts'
            """
        )
        if "balance" in {str(row["column_name"]) for row in columns}:
            await conn.execute(
                """
                UPDATE point_accounts
                SET permanent_points = balance
                WHERE permanent_points = 0 AND balance > 0
                """
            )

        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS invite_codes (
                id                    UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
                code                  TEXT NOT NULL UNIQUE,
                code_type             TEXT NOT NULL,
                plan_id               TEXT,
                topup_amount          INTEGER,
                note                  TEXT,
                created_by_user_id    UUID REFERENCES users(id) ON DELETE SET NULL,
                consumed_by_user_id   UUID REFERENCES users(id) ON DELETE SET NULL,
                consumed_by_email     TEXT,
                consumed_at           TIMESTAMPTZ,
                created_at            TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        await conn.execute("ALTER TABLE invite_codes ADD COLUMN IF NOT EXISTS plan_id TEXT")
        await conn.execute("ALTER TABLE invite_codes ALTER COLUMN topup_amount DROP NOT NULL")
        await conn.execute("ALTER TABLE invite_codes DROP CONSTRAINT IF EXISTS invite_codes_type_amount_match")
        await conn.execute("ALTER TABLE invite_codes DROP CONSTRAINT IF EXISTS invite_codes_payload_match")
        await conn.execute(
            """
            ALTER TABLE invite_codes ADD CONSTRAINT invite_codes_payload_match CHECK (
                (plan_id IN ('basic', 'pro') AND code_type = 'sub_' || plan_id AND topup_amount IS NULL)
                OR
                (topup_amount IN (1000, 2000, 3000, 4000, 5000)
                 AND code_type = 'topup_' || topup_amount::TEXT AND plan_id IS NULL)
            )
            """
        )

        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS point_transactions (
                id                 UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
                user_id            UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                amount             INTEGER NOT NULL CHECK (amount <> 0),
                total_after        BIGINT NOT NULL CHECK (total_after >= 0),
                type               TEXT NOT NULL,
                description        TEXT NOT NULL,
                invite_code_id     UUID REFERENCES invite_codes(id) ON DELETE SET NULL,
                operator_user_id   UUID REFERENCES users(id) ON DELETE SET NULL,
                idempotency_key    TEXT UNIQUE,
                created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        await conn.execute(
            "ALTER TABLE point_transactions ADD COLUMN IF NOT EXISTS total_after BIGINT NOT NULL DEFAULT 0"
        )
        await conn.execute(
            "ALTER TABLE point_transactions ADD COLUMN IF NOT EXISTS idempotency_key TEXT"
        )
        tx_columns = await conn.fetch(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'point_transactions'
            """
        )
        if "balance_after" in {str(row["column_name"]) for row in tx_columns}:
            await conn.execute(
                "UPDATE point_transactions SET total_after = balance_after WHERE total_after = 0"
            )

        for ddl in (
            "CREATE INDEX IF NOT EXISTS idx_invite_codes_created ON invite_codes(created_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_invite_codes_type ON invite_codes(code_type, created_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_invite_codes_consumed ON invite_codes(consumed_at, created_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_point_transactions_user ON point_transactions(user_id, created_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_point_transactions_invite ON point_transactions(invite_code_id)",
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_point_transactions_idempotency ON point_transactions(idempotency_key)",
        ):
            await conn.execute(ddl)
        await conn.execute(
            """
            DO $$ BEGIN
                CREATE TRIGGER trg_point_accounts_touch BEFORE UPDATE ON point_accounts
                    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();
            EXCEPTION WHEN duplicate_object THEN NULL; END $$
            """
        )
