-- ============================================================
--  Claude-compatible Skills
-- ============================================================

CREATE TABLE IF NOT EXISTS skills (
    id             UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    owner_user_id  UUID REFERENCES users(id) ON DELETE SET NULL,
    name           TEXT NOT NULL,
    title          TEXT,
    description    TEXT NOT NULL,
    visibility     TEXT NOT NULL DEFAULT 'private'
                   CHECK (visibility IN ('private','shared','official')),
    source         TEXT NOT NULL DEFAULT 'manual'
                   CHECK (source IN ('upload','manual','generated','official')),
    status         TEXT NOT NULL DEFAULT 'active'
                   CHECK (status IN ('draft','active','archived')),
    version        INTEGER NOT NULL DEFAULT 1,
    skill_md       TEXT NOT NULL,
    files          JSONB NOT NULL DEFAULT '{}'::jsonb,
    metadata       JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(owner_user_id, name, version)
);

CREATE INDEX IF NOT EXISTS idx_skills_owner ON skills(owner_user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_skills_market ON skills(visibility, status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_skills_name ON skills(name);

CREATE TABLE IF NOT EXISTS user_skills (
    user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    skill_id    UUID NOT NULL REFERENCES skills(id) ON DELETE CASCADE,
    enabled     BOOLEAN NOT NULL DEFAULT true,
    installed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, skill_id)
);

CREATE INDEX IF NOT EXISTS idx_user_skills_enabled ON user_skills(user_id, enabled);

DO $$ BEGIN
    CREATE TRIGGER trg_skills_touch BEFORE UPDATE ON skills
        FOR EACH ROW EXECUTE FUNCTION touch_updated_at();
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TRIGGER trg_user_skills_touch BEFORE UPDATE ON user_skills
        FOR EACH ROW EXECUTE FUNCTION touch_updated_at();
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
