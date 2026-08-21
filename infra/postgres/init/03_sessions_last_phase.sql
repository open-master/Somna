-- 已有数据库卷升级：agent-core 启动时也会 ADD COLUMN IF NOT EXISTS。
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS last_phase TEXT;
