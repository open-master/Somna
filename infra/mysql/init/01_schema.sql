-- ============================================================
--  Somna AI — MySQL 初始化（业务库）
--  职责: 用户 / 团队 / 订阅 / 计费 / 配额 / 模型偏好
--  （Agent runtime 数据走 Postgres）
-- ============================================================

SET NAMES utf8mb4;
SET FOREIGN_KEY_CHECKS = 0;

CREATE DATABASE IF NOT EXISTS somna_biz CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE somna_biz;

-- ============================================================
--  Users
-- ============================================================
CREATE TABLE IF NOT EXISTS users (
    id              CHAR(36) PRIMARY KEY,
    email           VARCHAR(255) NOT NULL UNIQUE,
    email_verified  TINYINT(1) NOT NULL DEFAULT 0,
    name            VARCHAR(128),
    avatar_url      VARCHAR(512),
    password_hash   VARCHAR(255),
    locale          VARCHAR(16) DEFAULT 'zh-CN',
    timezone        VARCHAR(64) DEFAULT 'Asia/Shanghai',
    status          VARCHAR(16) NOT NULL DEFAULT 'active',
    metadata        JSON,
    created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_users_created (created_at)
) ENGINE=InnoDB;

-- OAuth 关联（多 provider）
CREATE TABLE IF NOT EXISTS user_identities (
    id           CHAR(36) PRIMARY KEY,
    user_id      CHAR(36) NOT NULL,
    provider     VARCHAR(32) NOT NULL,
    provider_uid VARCHAR(255) NOT NULL,
    access_token TEXT,
    refresh_token TEXT,
    expires_at   DATETIME,
    created_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_identity (provider, provider_uid),
    INDEX idx_identity_user (user_id),
    CONSTRAINT fk_identity_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB;

-- ============================================================
--  Teams / Workspaces
-- ============================================================
CREATE TABLE IF NOT EXISTS teams (
    id          CHAR(36) PRIMARY KEY,
    name        VARCHAR(128) NOT NULL,
    slug        VARCHAR(64) NOT NULL UNIQUE,
    owner_id    CHAR(36) NOT NULL,
    plan        VARCHAR(32) NOT NULL DEFAULT 'free',
    status      VARCHAR(16) NOT NULL DEFAULT 'active',
    created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_team_owner (owner_id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS team_members (
    team_id    CHAR(36) NOT NULL,
    user_id    CHAR(36) NOT NULL,
    role       VARCHAR(16) NOT NULL DEFAULT 'member',  -- owner/admin/member/viewer
    joined_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (team_id, user_id),
    CONSTRAINT fk_tm_team FOREIGN KEY (team_id) REFERENCES teams(id) ON DELETE CASCADE,
    CONSTRAINT fk_tm_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB;

-- ============================================================
--  API Keys (供外部调用业务 API)
-- ============================================================
CREATE TABLE IF NOT EXISTS api_keys (
    id           CHAR(36) PRIMARY KEY,
    user_id      CHAR(36) NOT NULL,
    name         VARCHAR(128) NOT NULL,
    prefix       VARCHAR(12) NOT NULL,
    key_hash     VARCHAR(128) NOT NULL UNIQUE,
    scopes       JSON,
    last_used_at DATETIME,
    expires_at   DATETIME,
    revoked_at   DATETIME,
    created_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_apikey_user (user_id),
    CONSTRAINT fk_apikey_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB;

-- ============================================================
--  Subscriptions & Billing
-- ============================================================
CREATE TABLE IF NOT EXISTS plans (
    id           VARCHAR(32) PRIMARY KEY,       -- free / pro / team / enterprise
    name         VARCHAR(64) NOT NULL,
    price_usd    DECIMAL(10,2) NOT NULL DEFAULT 0,
    currency     VARCHAR(8) NOT NULL DEFAULT 'USD',
    features     JSON,
    limits       JSON,                          -- {max_sessions, max_tokens_per_day, max_sandboxes, ...}
    active       TINYINT(1) NOT NULL DEFAULT 1,
    created_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS subscriptions (
    id             CHAR(36) PRIMARY KEY,
    user_id        CHAR(36) NOT NULL,
    team_id        CHAR(36),
    plan_id        VARCHAR(32) NOT NULL,
    status         VARCHAR(16) NOT NULL DEFAULT 'active',
    current_period_start DATETIME,
    current_period_end   DATETIME,
    cancel_at_period_end TINYINT(1) DEFAULT 0,
    external_id    VARCHAR(128),                -- Stripe 等外部订阅 id
    metadata       JSON,
    created_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_sub_user (user_id),
    INDEX idx_sub_plan (plan_id),
    CONSTRAINT fk_sub_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    CONSTRAINT fk_sub_plan FOREIGN KEY (plan_id) REFERENCES plans(id)
) ENGINE=InnoDB;

-- 用量计量
CREATE TABLE IF NOT EXISTS usage_records (
    id           BIGINT AUTO_INCREMENT PRIMARY KEY,
    user_id      CHAR(36) NOT NULL,
    session_id   CHAR(36),
    kind         VARCHAR(32) NOT NULL,         -- llm_tokens / sandbox_seconds / storage_bytes
    model        VARCHAR(64),
    amount       DECIMAL(18,6) NOT NULL,
    cost_usd     DECIMAL(12,6) NOT NULL DEFAULT 0,
    metadata     JSON,
    occurred_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_usage_user_time (user_id, occurred_at),
    INDEX idx_usage_session   (session_id),
    INDEX idx_usage_kind      (kind)
) ENGINE=InnoDB;

-- ============================================================
--  User Preferences
-- ============================================================
CREATE TABLE IF NOT EXISTS user_preferences (
    user_id          CHAR(36) PRIMARY KEY,
    default_planner  VARCHAR(64) DEFAULT 'agent-planner',
    default_executor VARCHAR(64) DEFAULT 'agent-executor',
    default_coder    VARCHAR(64) DEFAULT 'agent-coder',
    theme            VARCHAR(16) DEFAULT 'system',
    locale           VARCHAR(16) DEFAULT 'zh-CN',
    custom_instructions TEXT,
    updated_at       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    CONSTRAINT fk_pref_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB;

-- ============================================================
--  Audit log
-- ============================================================
CREATE TABLE IF NOT EXISTS audit_logs (
    id          BIGINT AUTO_INCREMENT PRIMARY KEY,
    user_id     CHAR(36),
    action      VARCHAR(64) NOT NULL,
    resource    VARCHAR(128),
    ip          VARCHAR(64),
    user_agent  VARCHAR(512),
    metadata    JSON,
    created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_audit_user_time (user_id, created_at),
    INDEX idx_audit_action    (action)
) ENGINE=InnoDB;

-- ============================================================
--  Seed plans
-- ============================================================
INSERT IGNORE INTO plans (id, name, price_usd, features, limits) VALUES
    ('free', 'Free',   0,  JSON_OBJECT('sandbox', true, 'long_context', false),
                            JSON_OBJECT('max_sessions_per_day', 10, 'max_tokens_per_day', 100000)),
    ('pro',  'Pro',    29, JSON_OBJECT('sandbox', true, 'long_context', true,  'priority_queue', true),
                            JSON_OBJECT('max_sessions_per_day', 200, 'max_tokens_per_day', 5000000)),
    ('team', 'Team',   99, JSON_OBJECT('sandbox', true, 'long_context', true,  'priority_queue', true, 'seats', 5),
                            JSON_OBJECT('max_sessions_per_day', 1000, 'max_tokens_per_day', 30000000));

SET FOREIGN_KEY_CHECKS = 1;
