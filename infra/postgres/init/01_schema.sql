-- ============================================================
--  Somna AI — Postgres 初始化
--  职责:
--    1. Agent runtime 状态（LangGraph checkpoint 自动建表，这里只建我们自己的业务表）
--    2. 会话 / 事件 / Artifact / 沙盒记录
--    3. pgvector 兜底（小规模 RAG 可直接用）
--    4. LiteLLM 自己的库
-- ============================================================

-- 扩展
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS vector;

-- 为 LiteLLM 单独建库
CREATE DATABASE litellm;

-- ============================================================
--  Session & Agent State
-- ============================================================

-- 会话主表
CREATE TABLE IF NOT EXISTS sessions (
    id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id       UUID NOT NULL,
    title         TEXT NOT NULL DEFAULT 'Untitled',
    status        TEXT NOT NULL DEFAULT 'active'
                  CHECK (status IN ('active','running','paused','interrupted','stopped','done','error','archived')),
    sandbox_id    TEXT,
    planner_model TEXT,
    executor_model TEXT,
    workflow_id   TEXT,           -- Temporal workflow id
    run_id        TEXT,           -- Temporal run id
    metadata      JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_sessions_user      ON sessions(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_sessions_status    ON sessions(status);
CREATE INDEX IF NOT EXISTS idx_sessions_workflow  ON sessions(workflow_id);

-- 消息表（持久化对话记录，LangGraph checkpoint 补充）
CREATE TABLE IF NOT EXISTS messages (
    id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    session_id   UUID NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    role         TEXT NOT NULL CHECK (role IN ('user','assistant','tool','system')),
    content      JSONB NOT NULL,     -- 结构化：text / tool_call / tool_result / artifact
    tokens_in    INTEGER,
    tokens_out   INTEGER,
    cost_usd     NUMERIC(12,6),
    model        TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, created_at);

-- 事件流（Agent → UI，用于回放）
CREATE TABLE IF NOT EXISTS events (
    id          BIGSERIAL PRIMARY KEY,
    session_id  UUID NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    type        TEXT NOT NULL,
    payload     JSONB NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_events_session_time ON events(session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_events_type ON events(type);

-- TODO / Plan
CREATE TABLE IF NOT EXISTS todos (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    session_id  UUID NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    parent_id   UUID REFERENCES todos(id) ON DELETE CASCADE,
    seq         INTEGER NOT NULL,
    text        TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending','in_progress','done','failed','skipped')),
    result      JSONB,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_todos_session ON todos(session_id, seq);

-- 沙盒记录
CREATE TABLE IF NOT EXISTS sandboxes (
    id           TEXT PRIMARY KEY,               -- 容器 id
    session_id   UUID REFERENCES sessions(id) ON DELETE SET NULL,
    user_id      UUID,
    status       TEXT NOT NULL DEFAULT 'starting'
                 CHECK (status IN ('starting','running','idle','stopping','stopped','error')),
    image        TEXT,
    cpu_limit    TEXT,
    mem_limit    TEXT,
    workspace_volume TEXT,
    vnc_port     INTEGER,
    mcp_port     INTEGER,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_active  TIMESTAMPTZ NOT NULL DEFAULT now(),
    stopped_at   TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_sandboxes_session ON sandboxes(session_id);
CREATE INDEX IF NOT EXISTS idx_sandboxes_status  ON sandboxes(status);

-- 产物（文件/报告/代码仓库等）
CREATE TABLE IF NOT EXISTS artifacts (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    session_id  UUID NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    mime        TEXT,
    size_bytes  BIGINT,
    s3_key      TEXT NOT NULL,
    preview     TEXT,
    metadata    JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_artifacts_session ON artifacts(session_id);

-- ============================================================
--  Memory (Procedural / Episodic)  —— mem0 之外的自有结构
-- ============================================================

-- 小规模 RAG 的 pgvector 兜底表（Milvus 是主力）
CREATE TABLE IF NOT EXISTS rag_chunks (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    session_id  UUID,
    source      TEXT,
    content     TEXT NOT NULL,
    embedding   vector(1024),       -- bge-m3 / qwen3-embedding 默认 1024 维
    metadata    JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_rag_embedding ON rag_chunks
    USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
CREATE INDEX IF NOT EXISTS idx_rag_session ON rag_chunks(session_id);

-- 流程记忆（成功 trace，供未来重用）
CREATE TABLE IF NOT EXISTS procedures (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id     UUID,
    name        TEXT NOT NULL,
    description TEXT,
    steps       JSONB NOT NULL,    -- [{tool, args, expected}, ...]
    tags        TEXT[],
    success_count INTEGER DEFAULT 0,
    failure_count INTEGER DEFAULT 0,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ============================================================
--  Trigger: updated_at 自动更新
-- ============================================================
CREATE OR REPLACE FUNCTION touch_updated_at() RETURNS TRIGGER AS $$
BEGIN NEW.updated_at = now(); RETURN NEW; END; $$ LANGUAGE plpgsql;

DO $$ BEGIN
    CREATE TRIGGER trg_sessions_touch BEFORE UPDATE ON sessions
        FOR EACH ROW EXECUTE FUNCTION touch_updated_at();
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TRIGGER trg_todos_touch BEFORE UPDATE ON todos
        FOR EACH ROW EXECUTE FUNCTION touch_updated_at();
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TRIGGER trg_procedures_touch BEFORE UPDATE ON procedures
        FOR EACH ROW EXECUTE FUNCTION touch_updated_at();
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
