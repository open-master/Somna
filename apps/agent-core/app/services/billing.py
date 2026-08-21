"""Task classification, runtime prices, point reservation, metering, and settlement."""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from somna_events import TokenUsageEvent

from app.config import get_settings
from app.events.emitter import emit
from app.logging_setup import get_logger
from app.services.points import ensure_point_account
from app.storage.postgres import get_pool

log = get_logger(__name__)

BILLING_SETTINGS_KEY = "task_billing_v1"
TASK_CATEGORIES = ("chat", "research", "content_build", "code_build", "operate", "media")
TASK_CATEGORY_NAMES = {
    "chat": "对话与内容理解",
    "research": "搜索与分析",
    "content_build": "内容与办公交付",
    "code_build": "代码与工程构建",
    "operate": "操作与自动化",
    "media": "生成式媒体",
}
TASK_MODES = ("direct_answer", "research", "research_and_report", "build", "operate", "full_pipeline")
EFFORT_LEVELS = ("low", "medium", "high")
DELIVERABLE_TYPES = (
    "unspecified",
    "chat_answer",
    "direct_answer",
    "markdown_report",
    "file",
    "document",
    "spreadsheet",
    "presentation",
    "code",
    "web_app",
    "website",
    "image",
    "video",
    "audio",
    "multimodal",
    "browser_action",
)
VIDEO_TOOLS = frozenset({"wan_t2v", "wan_i2v", "wan_r2v", "wan_video_edit"})
MEDIA_TOOLS = VIDEO_TOOLS | {"wan_text2image", "minimax_tts", "visual_critique"}
IRREVERSIBLE_MEDIA_TOOLS = VIDEO_TOOLS | {"wan_text2image", "minimax_tts"}


class InsufficientPointsError(RuntimeError):
    def __init__(self, *, required: int, current: int) -> None:
        self.required = required
        self.current = current
        super().__init__(f"积分不足：需要冻结 {required}，当前可用 {current}")


def _bounded_int(value: Any, default: int, *, minimum: int = 0, maximum: int = 1_000_000) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = int(default)
    return max(minimum, min(parsed, maximum))


def default_billing_config() -> dict[str, Any]:
    settings = get_settings()
    return {
        "version": 1,
        "task_base": {
            "chat": int(settings.points_task_chat_base),
            "research": int(settings.points_task_research_base),
            "content_build": int(settings.points_task_content_build_base),
            "code_build": int(settings.points_task_code_build_base),
            "operate": int(settings.points_task_operate_base),
            "media": int(settings.points_task_media_base),
        },
        "effort_multiplier": {
            "low": int(settings.points_effort_low_multiplier),
            "medium": int(settings.points_effort_medium_multiplier),
            "high": int(settings.points_effort_high_multiplier),
        },
        "model_meter": {
            "reserve_points": int(settings.points_task_model_reserve),
            "input_tokens_per_point": int(settings.points_model_input_tokens_per_point),
            "output_tokens_per_point": int(settings.points_model_output_tokens_per_point),
        },
        "tool_costs": {
            "web_search_batch": int(settings.points_web_search_batch),
            "visual_critique": int(settings.points_visual_critique),
            "image_per_output": int(settings.points_image_per_output),
            "tts_per_1000_chars": int(settings.points_tts_per_1000_chars),
            "video_default_per_output": int(settings.points_video_default_per_output),
            "external_side_effect": int(settings.points_external_side_effect),
        },
        "reservation_ttl_seconds": int(settings.points_reservation_ttl_seconds),
    }


def normalize_billing_config(raw: dict[str, Any] | None) -> dict[str, Any]:
    defaults = default_billing_config()
    source = raw if isinstance(raw, dict) else {}
    task_raw = source.get("task_base") if isinstance(source.get("task_base"), dict) else {}
    effort_raw = source.get("effort_multiplier") if isinstance(source.get("effort_multiplier"), dict) else {}
    model_raw = source.get("model_meter") if isinstance(source.get("model_meter"), dict) else {}
    tool_raw = source.get("tool_costs") if isinstance(source.get("tool_costs"), dict) else {}
    return {
        "version": 1,
        "task_base": {
            key: _bounded_int(task_raw.get(key), defaults["task_base"][key], maximum=10_000)
            for key in TASK_CATEGORIES
        },
        "effort_multiplier": {
            key: _bounded_int(effort_raw.get(key), defaults["effort_multiplier"][key], minimum=1, maximum=20)
            for key in EFFORT_LEVELS
        },
        "model_meter": {
            "reserve_points": _bounded_int(
                model_raw.get("reserve_points"), defaults["model_meter"]["reserve_points"], maximum=10_000
            ),
            "input_tokens_per_point": _bounded_int(
                model_raw.get("input_tokens_per_point"),
                defaults["model_meter"]["input_tokens_per_point"],
                minimum=1,
                maximum=100_000_000,
            ),
            "output_tokens_per_point": _bounded_int(
                model_raw.get("output_tokens_per_point"),
                defaults["model_meter"]["output_tokens_per_point"],
                minimum=1,
                maximum=100_000_000,
            ),
        },
        "tool_costs": {
            key: _bounded_int(tool_raw.get(key), defaults["tool_costs"][key], maximum=100_000)
            for key in defaults["tool_costs"]
        },
        "reservation_ttl_seconds": _bounded_int(
            source.get("reservation_ttl_seconds"),
            defaults["reservation_ttl_seconds"],
            minimum=60,
            maximum=7 * 24 * 3600,
        ),
    }


def normalize_task_mode(value: Any) -> str:
    candidate = str(value or "").strip().lower()
    return candidate if candidate in TASK_MODES else "full_pipeline"


def normalize_effort_level(value: Any) -> str:
    candidate = str(value or "").strip().lower()
    return candidate if candidate in EFFORT_LEVELS else "medium"


def normalize_deliverable_type(value: Any) -> str:
    candidate = str(value or "").strip().lower()
    return candidate if candidate in DELIVERABLE_TYPES else "unspecified"


def classify_task(frame: dict[str, Any] | None) -> str:
    frame = frame if isinstance(frame, dict) else {}
    mode = normalize_task_mode(frame.get("task_mode"))
    deliverable = normalize_deliverable_type(frame.get("deliverable_type"))
    if mode == "direct_answer":
        return "chat"
    if deliverable in {"image", "video", "audio", "multimodal"}:
        return "media"
    if mode == "research":
        return "research"
    if mode == "research_and_report":
        return "content_build"
    if mode == "operate" or deliverable == "browser_action":
        return "operate"
    if deliverable in {"code", "web_app", "website"}:
        return "code_build"
    if mode == "build" or deliverable in {
        "markdown_report",
        "file",
        "document",
        "spreadsheet",
        "presentation",
    }:
        return "content_build"
    return "code_build" if mode == "full_pipeline" else "chat"


def reservation_quote(config: dict[str, Any], category: str, effort: str) -> tuple[int, int]:
    normalized = normalize_billing_config(config)
    category = category if category in TASK_CATEGORIES else "chat"
    effort = normalize_effort_level(effort)
    base = int(normalized["task_base"][category])
    reserve = base * int(normalized["effort_multiplier"][effort]) + int(
        normalized["model_meter"]["reserve_points"]
    )
    return base, max(base, reserve)


def model_usage_points(config: dict[str, Any], input_tokens: int, output_tokens: int) -> int:
    normalized = normalize_billing_config(config)
    meter = normalized["model_meter"]
    weighted = max(0, int(input_tokens)) / meter["input_tokens_per_point"]
    weighted += max(0, int(output_tokens)) / meter["output_tokens_per_point"]
    return int(math.ceil(weighted)) if weighted > 0 else 0


def tool_point_quote(config: dict[str, Any], tool_name: str, args: dict[str, Any] | None) -> int:
    costs = normalize_billing_config(config)["tool_costs"]
    args = args if isinstance(args, dict) else {}
    if tool_name == "search":
        return costs["web_search_batch"]
    if tool_name == "visual_critique":
        return costs["visual_critique"]
    if tool_name == "wan_text2image":
        return max(1, min(_bounded_int(args.get("n"), 1, minimum=1, maximum=4), 4)) * costs[
            "image_per_output"
        ]
    if tool_name == "minimax_tts":
        chars = len(str(args.get("text") or "").strip())
        return max(1, math.ceil(chars / 1000)) * costs["tts_per_1000_chars"] if chars else 0
    if tool_name in VIDEO_TOOLS:
        return costs["video_default_per_output"]
    return 0


def tool_usage_points(
    config: dict[str, Any],
    tool_name: str,
    args: dict[str, Any] | None,
    result: Any,
) -> int:
    if not bool(getattr(result, "ok", False)):
        return 0
    output = getattr(result, "output", None)
    if tool_name == "search" and isinstance(output, dict) and str(output.get("provider") or "").lower() == "mock":
        return 0
    if tool_name == "wan_text2image" and isinstance(output, dict) and isinstance(output.get("paths"), list):
        count = max(0, min(len(output["paths"]), 4))
        return count * normalize_billing_config(config)["tool_costs"]["image_per_output"]
    return tool_point_quote(config, tool_name, args)


def tool_bills_on_failure(tool_name: str) -> bool:
    return tool_name in IRREVERSIBLE_MEDIA_TOOLS


async def ensure_billing_tables() -> None:
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS point_billing_settings (
                key TEXT PRIMARY KEY, value JSONB NOT NULL DEFAULT '{}'::jsonb,
                version INTEGER NOT NULL DEFAULT 1,
                updated_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );
            CREATE TABLE IF NOT EXISTS point_billing_runs (
                run_id TEXT PRIMARY KEY,
                session_id UUID NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                task_category TEXT, task_mode TEXT, effort_level TEXT, deliverable_type TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                reserved_points INTEGER NOT NULL DEFAULT 0,
                daily_amount INTEGER NOT NULL DEFAULT 0,
                monthly_amount INTEGER NOT NULL DEFAULT 0,
                permanent_amount INTEGER NOT NULL DEFAULT 0,
                actual_points INTEGER NOT NULL DEFAULT 0,
                base_points INTEGER NOT NULL DEFAULT 0,
                model_points INTEGER NOT NULL DEFAULT 0,
                tool_points INTEGER NOT NULL DEFAULT 0,
                pricing_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
                outcome TEXT, expires_at TIMESTAMPTZ, authorized_at TIMESTAMPTZ,
                settled_at TIMESTAMPTZ, released_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );
            CREATE TABLE IF NOT EXISTS point_billing_usage (
                id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
                run_id TEXT NOT NULL REFERENCES point_billing_runs(run_id) ON DELETE CASCADE,
                idempotency_key TEXT NOT NULL UNIQUE,
                kind TEXT NOT NULL, name TEXT NOT NULL, model TEXT,
                input_tokens INTEGER NOT NULL DEFAULT 0,
                output_tokens INTEGER NOT NULL DEFAULT 0,
                cost_usd NUMERIC(16,8) NOT NULL DEFAULT 0,
                points INTEGER NOT NULL DEFAULT 0,
                bill_on_failure BOOLEAN NOT NULL DEFAULT FALSE,
                metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );
            CREATE TABLE IF NOT EXISTS point_billing_reservations (
                idempotency_key TEXT PRIMARY KEY,
                run_id TEXT NOT NULL REFERENCES point_billing_runs(run_id) ON DELETE CASCADE,
                tool_name TEXT NOT NULL,
                points INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );
            CREATE INDEX IF NOT EXISTS idx_point_billing_runs_user
                ON point_billing_runs(user_id, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_point_billing_runs_status_expiry
                ON point_billing_runs(status, expires_at);
            CREATE INDEX IF NOT EXISTS idx_point_billing_usage_run
                ON point_billing_usage(run_id, created_at);
            """
        )
        await conn.execute(
            "ALTER TABLE point_billing_runs ADD COLUMN IF NOT EXISTS pricing_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb"
        )


async def get_runtime_billing_config(conn: Any) -> dict[str, Any]:
    row = await conn.fetchrow("SELECT value FROM point_billing_settings WHERE key = $1", BILLING_SETTINGS_KEY)
    if row is None:
        config = normalize_billing_config(None)
        await conn.execute(
            "INSERT INTO point_billing_settings (key, value) VALUES ($1, $2::jsonb) ON CONFLICT (key) DO NOTHING",
            BILLING_SETTINGS_KEY,
            json.dumps(config, ensure_ascii=False),
        )
        return config
    value = row["value"]
    if isinstance(value, str):
        value = json.loads(value)
    normalized = normalize_billing_config(value if isinstance(value, dict) else None)
    if normalized != value:
        await conn.execute(
            "UPDATE point_billing_settings SET value = $2::jsonb, updated_at = now() WHERE key = $1",
            BILLING_SETTINGS_KEY,
            json.dumps(normalized, ensure_ascii=False),
        )
    return normalized


async def update_runtime_billing_config(conn: Any, payload: dict[str, Any], *, updated_by_user_id: Any) -> dict[str, Any]:
    current = await get_runtime_billing_config(conn)
    merged = dict(current)
    for section in ("task_base", "effort_multiplier", "model_meter", "tool_costs"):
        incoming = payload.get(section)
        if isinstance(incoming, dict):
            merged[section] = {**current.get(section, {}), **incoming}
    if "reservation_ttl_seconds" in payload:
        merged["reservation_ttl_seconds"] = payload["reservation_ttl_seconds"]
    normalized = normalize_billing_config(merged)
    await conn.execute(
        """
        INSERT INTO point_billing_settings (key, value, updated_by_user_id)
        VALUES ($1, $2::jsonb, $3)
        ON CONFLICT (key) DO UPDATE SET
            value = EXCLUDED.value, version = point_billing_settings.version + 1,
            updated_by_user_id = EXCLUDED.updated_by_user_id, updated_at = now()
        """,
        BILLING_SETTINGS_KEY,
        json.dumps(normalized, ensure_ascii=False),
        updated_by_user_id,
    )
    return normalized


async def reset_runtime_billing_config(conn: Any, *, updated_by_user_id: Any) -> dict[str, Any]:
    return await update_runtime_billing_config(conn, default_billing_config(), updated_by_user_id=updated_by_user_id)


async def create_billing_run(*, run_id: str, session_id: Any, user_id: Any) -> None:
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO point_billing_runs (run_id, session_id, user_id)
            VALUES ($1, $2, $3)
            ON CONFLICT (run_id) DO NOTHING
            """,
            run_id,
            session_id,
            user_id,
        )


def _account_total(row: Any) -> int:
    return int(row["daily_points"]) + int(row["monthly_points"]) + int(row["permanent_points"])


def _debit_breakdown(row: Any, amount: int) -> tuple[int, int, int]:
    daily = min(int(row["daily_points"]), amount)
    remaining = amount - daily
    monthly = min(int(row["monthly_points"]), remaining)
    permanent = remaining - monthly
    return daily, monthly, permanent


async def _release_expired_for_locked_account(conn: Any, account: Any) -> Any:
    rows = await conn.fetch(
        """
        SELECT * FROM point_billing_runs
        WHERE user_id = $1 AND status = 'authorized' AND expires_at <= now()
        FOR UPDATE
        """,
        account["user_id"],
    )
    if not rows:
        return account
    daily_restore = monthly_restore = permanent_restore = 0
    today = datetime.now(UTC).date()
    month_start = today.replace(day=1)
    for row in rows:
        created = row["created_at"]
        if created.tzinfo is None:
            created = created.replace(tzinfo=UTC)
        same_day = created.astimezone(UTC).date() == today
        same_month = created.astimezone(UTC).date().replace(day=1) == month_start
        d = int(row["daily_amount"])
        m = int(row["monthly_amount"])
        p = int(row["permanent_amount"])
        daily_restore += d if same_day else 0
        monthly_restore += m if same_month else 0
        permanent_restore += p + (0 if same_day else d) + (0 if same_month else m)
        await conn.execute(
            """
            UPDATE point_billing_runs
            SET status = 'released', outcome = 'expired', released_at = now(), updated_at = now()
            WHERE run_id = $1
            """,
            row["run_id"],
        )
    return await conn.fetchrow(
        """
        UPDATE point_accounts
        SET daily_points = daily_points + $2,
            monthly_points = monthly_points + $3,
            permanent_points = permanent_points + $4,
            updated_at = now()
        WHERE user_id = $1
        RETURNING *
        """,
        account["user_id"],
        daily_restore,
        monthly_restore,
        permanent_restore,
    )


async def authorize_billing_run(
    *,
    run_id: str,
    frame: dict[str, Any],
) -> dict[str, Any]:
    pool = get_pool()
    async with pool.acquire() as conn, conn.transaction():
        preview = await conn.fetchrow("SELECT user_id, status FROM point_billing_runs WHERE run_id = $1", run_id)
        if preview is None:
            raise RuntimeError(f"billing run {run_id!r} is missing")
        mode = normalize_task_mode(frame.get("task_mode"))
        effort = normalize_effort_level(frame.get("effort_level"))
        deliverable = normalize_deliverable_type(frame.get("deliverable_type"))
        category = classify_task({**frame, "task_mode": mode, "effort_level": effort, "deliverable_type": deliverable})
        if bool(frame.get("needs_clarification")):
            run = await conn.fetchrow("SELECT * FROM point_billing_runs WHERE run_id = $1 FOR UPDATE", run_id)
            if run is None:
                raise RuntimeError(f"billing run {run_id!r} is missing")
            if str(run["status"]) != "pending":
                return dict(run)
            updated = await conn.fetchrow(
                """
                UPDATE point_billing_runs
                SET task_category = $2, task_mode = $3, effort_level = $4, deliverable_type = $5,
                    status = 'released', outcome = 'clarification', released_at = now(), updated_at = now()
                WHERE run_id = $1 RETURNING *
                """,
                run_id,
                category,
                mode,
                effort,
                deliverable,
            )
            return dict(updated)

        account = await ensure_point_account(conn, preview["user_id"], for_update=True)
        account = await _release_expired_for_locked_account(conn, account)
        run = await conn.fetchrow("SELECT * FROM point_billing_runs WHERE run_id = $1 FOR UPDATE", run_id)
        if run is None:
            raise RuntimeError(f"billing run {run_id!r} is missing")
        if str(run["status"]) == "authorized":
            return dict(run)
        if str(run["status"]) != "pending":
            return dict(run)
        config = await get_runtime_billing_config(conn)
        base, reserve = reservation_quote(config, category, effort)
        current = _account_total(account)
        if current < reserve:
            raise InsufficientPointsError(required=reserve, current=current)
        daily, monthly, permanent = _debit_breakdown(account, reserve)
        await conn.execute(
            """
            UPDATE point_accounts
            SET daily_points = daily_points - $2,
                monthly_points = monthly_points - $3,
                permanent_points = permanent_points - $4,
                updated_at = now()
            WHERE user_id = $1
            """,
            run["user_id"],
            daily,
            monthly,
            permanent,
        )
        updated = await conn.fetchrow(
            """
            UPDATE point_billing_runs
            SET task_category = $2, task_mode = $3, effort_level = $4, deliverable_type = $5,
                status = 'authorized', reserved_points = $6,
                daily_amount = $7, monthly_amount = $8, permanent_amount = $9,
                base_points = $10, pricing_snapshot = $11::jsonb,
                expires_at = now() + make_interval(secs => $12),
                authorized_at = now(), updated_at = now()
            WHERE run_id = $1 RETURNING *
            """,
            run_id,
            category,
            mode,
            effort,
            deliverable,
            reserve,
            daily,
            monthly,
            permanent,
            base,
            json.dumps(config, ensure_ascii=False),
            int(config["reservation_ttl_seconds"]),
        )
        return dict(updated)


def _config_from_run(run: Any) -> dict[str, Any]:
    raw = run["pricing_snapshot"]
    if isinstance(raw, str):
        raw = json.loads(raw)
    return normalize_billing_config(raw if isinstance(raw, dict) else None)


async def reserve_tool_points(
    *,
    run_id: str,
    idempotency_key: str,
    tool_name: str,
    args: dict[str, Any],
) -> tuple[bool, int, int]:
    pool = get_pool()
    async with pool.acquire() as conn, conn.transaction():
        preview = await conn.fetchrow("SELECT user_id, status FROM point_billing_runs WHERE run_id = $1", run_id)
        if preview is None:
            return True, 0, 0
        if str(preview["status"]) != "authorized":
            return False, 0, 0
        account = await ensure_point_account(conn, preview["user_id"], for_update=True)
        run = await conn.fetchrow("SELECT * FROM point_billing_runs WHERE run_id = $1 FOR UPDATE", run_id)
        if run is None:
            return True, 0, 0
        if str(run["status"]) != "authorized":
            return False, 0, _account_total(account)
        existing = await conn.fetchrow(
            "SELECT points FROM point_billing_reservations WHERE idempotency_key = $1",
            idempotency_key,
        )
        if existing is not None:
            return True, int(existing["points"]), _account_total(account)
        config = _config_from_run(run)
        quote = tool_point_quote(config, tool_name, args)
        if quote <= 0:
            return True, 0, 0
        current = _account_total(account)
        if current < quote:
            return False, quote, current
        daily, monthly, permanent = _debit_breakdown(account, quote)
        await conn.execute(
            """
            UPDATE point_accounts
            SET daily_points = daily_points - $2,
                monthly_points = monthly_points - $3,
                permanent_points = permanent_points - $4,
                updated_at = now()
            WHERE user_id = $1
            """,
            run["user_id"],
            daily,
            monthly,
            permanent,
        )
        await conn.execute(
            """
            UPDATE point_billing_runs
            SET reserved_points = reserved_points + $2,
                daily_amount = daily_amount + $3,
                monthly_amount = monthly_amount + $4,
                permanent_amount = permanent_amount + $5,
                expires_at = now() + make_interval(secs => $6),
                updated_at = now()
            WHERE run_id = $1
            """,
            run_id,
            quote,
            daily,
            monthly,
            permanent,
            int(config["reservation_ttl_seconds"]),
        )
        await conn.execute(
            """
            INSERT INTO point_billing_reservations (idempotency_key, run_id, tool_name, points)
            VALUES ($1, $2, $3, $4)
            """,
            idempotency_key,
            run_id,
            tool_name,
            quote,
        )
        return True, quote, current


async def record_model_usage(
    *,
    run_id: str | None,
    idempotency_key: str,
    name: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cost_usd: float = 0.0,
) -> None:
    if not run_id or (input_tokens <= 0 and output_tokens <= 0 and cost_usd <= 0):
        return
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO point_billing_usage
                (run_id, idempotency_key, kind, name, model, input_tokens, output_tokens, cost_usd)
            SELECT $1, $2, 'model', $3, $4, $5, $6, $7
            WHERE EXISTS (SELECT 1 FROM point_billing_runs WHERE run_id = $1)
            ON CONFLICT (idempotency_key) DO NOTHING
            """,
            run_id,
            idempotency_key,
            name,
            model,
            max(0, int(input_tokens)),
            max(0, int(output_tokens)),
            Decimal(str(max(0.0, float(cost_usd)))),
        )


async def emit_model_usage(
    *,
    session_id: Any,
    run_id: str | None,
    usage_key: str,
    phase: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cost_usd: float = 0.0,
) -> None:
    if input_tokens <= 0 and output_tokens <= 0 and cost_usd <= 0:
        return
    await record_model_usage(
        run_id=run_id,
        idempotency_key=usage_key,
        name=phase,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=cost_usd,
    )
    await emit(
        TokenUsageEvent(
            session_id=session_id,
            run_id=run_id,
            model=model,
            input=max(0, int(input_tokens)),
            output=max(0, int(output_tokens)),
            cost_usd=max(0.0, float(cost_usd)),
        )
    )


async def record_tool_usage(
    *,
    run_id: str | None,
    idempotency_key: str,
    tool_name: str,
    args: dict[str, Any],
    result: Any,
) -> int:
    if not run_id or not bool(getattr(result, "ok", False)):
        return 0
    pool = get_pool()
    async with pool.acquire() as conn:
        run = await conn.fetchrow("SELECT pricing_snapshot FROM point_billing_runs WHERE run_id = $1", run_id)
        if run is None:
            return 0
        points = tool_usage_points(_config_from_run(run), tool_name, args, result)
        await conn.execute(
            """
            INSERT INTO point_billing_usage
                (run_id, idempotency_key, kind, name, points, bill_on_failure, metadata)
            VALUES ($1, $2, 'tool', $3, $4, $5, $6::jsonb)
            ON CONFLICT (idempotency_key) DO NOTHING
            """,
            run_id,
            idempotency_key,
            tool_name,
            points,
            tool_bills_on_failure(tool_name),
            json.dumps({"args": args}, ensure_ascii=False, default=str),
        )
        return points


async def _restore_unused(conn: Any, run: Any, unused: int) -> None:
    if unused <= 0:
        return
    billed = int(run["reserved_points"]) - unused
    daily_used = min(int(run["daily_amount"]), billed)
    remaining = billed - daily_used
    monthly_used = min(int(run["monthly_amount"]), remaining)
    permanent_used = max(0, remaining - monthly_used)
    daily_refund = int(run["daily_amount"]) - daily_used
    monthly_refund = int(run["monthly_amount"]) - monthly_used
    permanent_refund = int(run["permanent_amount"]) - permanent_used
    created = run["created_at"]
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    today = datetime.now(UTC).date()
    same_day = created.astimezone(UTC).date() == today
    same_month = created.astimezone(UTC).date().replace(day=1) == today.replace(day=1)
    daily_restore = daily_refund if same_day else 0
    monthly_restore = monthly_refund if same_month else 0
    permanent_restore = permanent_refund
    permanent_restore += 0 if same_day else daily_refund
    permanent_restore += 0 if same_month else monthly_refund
    await conn.execute(
        """
        UPDATE point_accounts
        SET daily_points = daily_points + $2,
            monthly_points = monthly_points + $3,
            permanent_points = permanent_points + $4,
            updated_at = now()
        WHERE user_id = $1
        """,
        run["user_id"],
        daily_restore,
        monthly_restore,
        permanent_restore,
    )


async def settle_billing_run(*, run_id: str | None, outcome: str) -> dict[str, Any] | None:
    if not run_id:
        return None
    normalized_outcome = outcome if outcome in {"success", "clarification", "cancelled", "failure"} else "failure"
    pool = get_pool()
    async with pool.acquire() as conn, conn.transaction():
        preview = await conn.fetchrow("SELECT user_id, status FROM point_billing_runs WHERE run_id = $1", run_id)
        if preview is None:
            return None
        if str(preview["status"]) == "pending":
            run = await conn.fetchrow("SELECT * FROM point_billing_runs WHERE run_id = $1 FOR UPDATE", run_id)
            if run is None:
                return None
            updated = await conn.fetchrow(
                """
                UPDATE point_billing_runs
                SET status = 'released', outcome = $2, released_at = now(), updated_at = now()
                WHERE run_id = $1 RETURNING *
                """,
                run_id,
                normalized_outcome,
            )
            return dict(updated)
        await ensure_point_account(conn, preview["user_id"], for_update=True)
        run = await conn.fetchrow("SELECT * FROM point_billing_runs WHERE run_id = $1 FOR UPDATE", run_id)
        if run is None:
            return None
        if str(run["status"]) in {"settled", "released", "failed", "cancelled"}:
            return dict(run)
        usage = await conn.fetchrow(
            """
            SELECT COALESCE(sum(input_tokens), 0) AS input_tokens,
                   COALESCE(sum(output_tokens), 0) AS output_tokens,
                   COALESCE(sum(points) FILTER (WHERE kind = 'tool'), 0) AS tool_points,
                   COALESCE(sum(points) FILTER (WHERE kind = 'tool' AND bill_on_failure), 0) AS failure_tool_points
            FROM point_billing_usage WHERE run_id = $1
            """,
            run_id,
        )
        config = _config_from_run(run)
        model_points = model_usage_points(config, int(usage["input_tokens"]), int(usage["output_tokens"]))
        all_tool_points = int(usage["tool_points"])
        if normalized_outcome == "success":
            base_points = int(run["base_points"])
            tool_points = all_tool_points
            charge_model_points = model_points
        elif normalized_outcome == "cancelled":
            base_points = 0
            tool_points = all_tool_points
            charge_model_points = model_points
        elif normalized_outcome == "failure":
            base_points = 0
            tool_points = int(usage["failure_tool_points"])
            charge_model_points = 0
        else:
            base_points = tool_points = charge_model_points = 0
        calculated = base_points + charge_model_points + tool_points
        billed = min(calculated, int(run["reserved_points"]))
        unused = int(run["reserved_points"]) - billed
        await _restore_unused(conn, run, unused)
        account = await conn.fetchrow("SELECT * FROM point_accounts WHERE user_id = $1", run["user_id"])
        total_after = _account_total(account)
        if billed > 0:
            await conn.execute(
                """
                INSERT INTO point_transactions
                    (user_id, amount, total_after, type, description, idempotency_key)
                VALUES ($1, $2, $3, 'task_consume', $4, $5)
                ON CONFLICT (idempotency_key) DO NOTHING
                """,
                run["user_id"],
                -billed,
                total_after,
                (
                    f"{TASK_CATEGORY_NAMES.get(str(run['task_category']), 'Agent 任务')}："
                    f"基础 {base_points} / 模型 {charge_model_points} / 工具 {tool_points}"
                ),
                f"billing:{run_id}",
            )
        status = "settled" if billed > 0 or normalized_outcome == "success" else "released"
        if normalized_outcome == "cancelled":
            status = "cancelled"
        elif normalized_outcome == "failure":
            status = "failed"
        updated = await conn.fetchrow(
            """
            UPDATE point_billing_runs
            SET status = $2, outcome = $3, actual_points = $4,
                base_points = $5, model_points = $6, tool_points = $7,
                settled_at = CASE WHEN $4 > 0 THEN now() ELSE settled_at END,
                released_at = CASE WHEN $4 = 0 THEN now() ELSE released_at END,
                updated_at = now()
            WHERE run_id = $1 RETURNING *
            """,
            run_id,
            status,
            normalized_outcome,
            billed,
            base_points,
            charge_model_points,
            tool_points,
        )
        if calculated > billed:
            log.warning(
                "points.billing_capped",
                run_id=run_id,
                calculated=calculated,
                billed=billed,
                reserved=int(run["reserved_points"]),
            )
        return dict(updated)


async def mark_billing_run_unstarted(*, run_id: str, outcome: str = "failure") -> None:
    await settle_billing_run(run_id=run_id, outcome=outcome)


async def release_expired_billing_reservations(*, user_id: Any) -> dict[str, Any]:
    """Return stale task holds before an account balance is displayed or reused."""
    pool = get_pool()
    async with pool.acquire() as conn, conn.transaction():
        account = await ensure_point_account(conn, user_id, for_update=True)
        account = await _release_expired_for_locked_account(conn, account)
        return dict(account)
