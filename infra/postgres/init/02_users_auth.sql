-- 用户表：邮箱+密码；google_sub 预留给后续 Google OAuth 绑定
-- email 在应用层规范为小写，列上唯一约束
CREATE TABLE IF NOT EXISTS users (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    email           TEXT NOT NULL UNIQUE,
    username        TEXT,
    password_hash   TEXT,
    google_sub      TEXT UNIQUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_users_email ON users (email);

DO $$ BEGIN
    CREATE TRIGGER trg_users_touch BEFORE UPDATE ON users
        FOR EACH ROW EXECUTE FUNCTION touch_updated_at();
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
