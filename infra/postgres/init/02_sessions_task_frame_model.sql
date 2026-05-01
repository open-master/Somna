-- 已有数据库卷升级：若 01 已执行过，在 agent-core 连接的业务库上执行本语句一次即可。
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS task_frame_model TEXT;
