-- ============================================================
--  Plan points, point ledger, and one-time invite codes
-- ============================================================

CREATE TABLE IF NOT EXISTS point_accounts (
    user_id               UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    plan_type             TEXT NOT NULL DEFAULT 'free'
                          CHECK (plan_type IN ('free', 'basic', 'pro')),
    plan_expire_at        TIMESTAMPTZ,
    daily_points          BIGINT NOT NULL DEFAULT 10 CHECK (daily_points >= 0),
    monthly_points        BIGINT NOT NULL DEFAULT 50 CHECK (monthly_points >= 0),
    permanent_points      BIGINT NOT NULL DEFAULT 0 CHECK (permanent_points >= 0),
    last_daily_reset_at   DATE,
    last_monthly_reset_at DATE,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS invite_codes (
    id                    UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    code                  TEXT NOT NULL UNIQUE,
    code_type             TEXT NOT NULL CHECK (
                              code_type IN (
                                  'sub_basic', 'sub_pro',
                                  'topup_1000', 'topup_2000', 'topup_3000',
                                  'topup_4000', 'topup_5000'
                              )
                          ),
    plan_id               TEXT CHECK (plan_id IS NULL OR plan_id IN ('basic', 'pro')),
    topup_amount          INTEGER CHECK (
                              topup_amount IS NULL OR
                              topup_amount IN (1000, 2000, 3000, 4000, 5000)
                          ),
    note                  TEXT,
    created_by_user_id    UUID REFERENCES users(id) ON DELETE SET NULL,
    consumed_by_user_id   UUID REFERENCES users(id) ON DELETE SET NULL,
    consumed_by_email     TEXT,
    consumed_at           TIMESTAMPTZ,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT invite_codes_payload_match CHECK (
        (plan_id IN ('basic', 'pro') AND code_type = 'sub_' || plan_id AND topup_amount IS NULL)
        OR
        (topup_amount IN (1000, 2000, 3000, 4000, 5000)
         AND code_type = 'topup_' || topup_amount::TEXT AND plan_id IS NULL)
    )
);

CREATE INDEX IF NOT EXISTS idx_invite_codes_created ON invite_codes(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_invite_codes_type ON invite_codes(code_type, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_invite_codes_consumed ON invite_codes(consumed_at, created_at DESC);

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
);

CREATE INDEX IF NOT EXISTS idx_point_transactions_user
    ON point_transactions(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_point_transactions_invite
    ON point_transactions(invite_code_id);

CREATE TABLE IF NOT EXISTS point_billing_settings (
    key                 TEXT PRIMARY KEY,
    value               JSONB NOT NULL DEFAULT '{}'::jsonb,
    version             INTEGER NOT NULL DEFAULT 1,
    updated_by_user_id  UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS point_billing_runs (
    run_id               TEXT PRIMARY KEY,
    session_id           UUID NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    user_id              UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    task_category        TEXT,
    task_mode            TEXT,
    effort_level         TEXT,
    deliverable_type     TEXT,
    status               TEXT NOT NULL DEFAULT 'pending'
                         CHECK (status IN ('pending','authorized','settled','released','failed','cancelled')),
    reserved_points      INTEGER NOT NULL DEFAULT 0 CHECK (reserved_points >= 0),
    daily_amount         INTEGER NOT NULL DEFAULT 0 CHECK (daily_amount >= 0),
    monthly_amount       INTEGER NOT NULL DEFAULT 0 CHECK (monthly_amount >= 0),
    permanent_amount     INTEGER NOT NULL DEFAULT 0 CHECK (permanent_amount >= 0),
    actual_points        INTEGER NOT NULL DEFAULT 0 CHECK (actual_points >= 0),
    base_points          INTEGER NOT NULL DEFAULT 0 CHECK (base_points >= 0),
    model_points         INTEGER NOT NULL DEFAULT 0 CHECK (model_points >= 0),
    tool_points          INTEGER NOT NULL DEFAULT 0 CHECK (tool_points >= 0),
    pricing_snapshot     JSONB NOT NULL DEFAULT '{}'::jsonb,
    outcome              TEXT,
    expires_at           TIMESTAMPTZ,
    authorized_at        TIMESTAMPTZ,
    settled_at           TIMESTAMPTZ,
    released_at          TIMESTAMPTZ,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_point_billing_runs_user
    ON point_billing_runs(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_point_billing_runs_status_expiry
    ON point_billing_runs(status, expires_at);

CREATE TABLE IF NOT EXISTS point_billing_usage (
    id                   UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    run_id               TEXT NOT NULL REFERENCES point_billing_runs(run_id) ON DELETE CASCADE,
    idempotency_key      TEXT NOT NULL UNIQUE,
    kind                 TEXT NOT NULL CHECK (kind IN ('model','tool')),
    name                 TEXT NOT NULL,
    model                TEXT,
    input_tokens         INTEGER NOT NULL DEFAULT 0 CHECK (input_tokens >= 0),
    output_tokens        INTEGER NOT NULL DEFAULT 0 CHECK (output_tokens >= 0),
    cost_usd             NUMERIC(16,8) NOT NULL DEFAULT 0 CHECK (cost_usd >= 0),
    points               INTEGER NOT NULL DEFAULT 0 CHECK (points >= 0),
    bill_on_failure      BOOLEAN NOT NULL DEFAULT FALSE,
    metadata             JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_point_billing_usage_run
    ON point_billing_usage(run_id, created_at);

CREATE TABLE IF NOT EXISTS point_billing_reservations (
    idempotency_key      TEXT PRIMARY KEY,
    run_id               TEXT NOT NULL REFERENCES point_billing_runs(run_id) ON DELETE CASCADE,
    tool_name             TEXT NOT NULL,
    points                INTEGER NOT NULL DEFAULT 0 CHECK (points >= 0),
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

DO $$ BEGIN
    CREATE TRIGGER trg_point_accounts_touch BEFORE UPDATE ON point_accounts
        FOR EACH ROW EXECUTE FUNCTION touch_updated_at();
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TRIGGER trg_point_billing_settings_touch BEFORE UPDATE ON point_billing_settings
        FOR EACH ROW EXECUTE FUNCTION touch_updated_at();
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
