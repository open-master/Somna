-- 账户状态：仅 active 可登录与调用需鉴权接口
ALTER TABLE users
    ADD COLUMN IF NOT EXISTS account_status TEXT NOT NULL DEFAULT 'active';

ALTER TABLE users DROP CONSTRAINT IF EXISTS users_account_status_check;
ALTER TABLE users ADD CONSTRAINT users_account_status_check CHECK (account_status IN ('active', 'disabled'));

CREATE INDEX IF NOT EXISTS idx_users_account_status ON users (account_status);
