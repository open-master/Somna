"""Application settings loaded from environment / .env."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../../.env", "/.env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---- Runtime ----
    env: Literal["dev", "staging", "prod"] = "dev"
    log_level: str = "INFO"
    service_name: str = "somna-agent-core"

    # ---- Postgres ----
    postgres_url: str = Field(alias="POSTGRES_URL")

    # ---- Redis ----
    redis_url: str = Field(alias="REDIS_URL")

    # ---- NATS ----
    nats_url: str = Field(alias="NATS_URL")

    # ---- LiteLLM ----
    litellm_url: str = Field(alias="LITELLM_URL")
    litellm_master_key: str = Field(alias="LITELLM_MASTER_KEY")

    # ---- Temporal ----
    temporal_host: str = Field(default="temporal:7233", alias="TEMPORAL_HOST")
    temporal_namespace: str = Field(default="default", alias="TEMPORAL_NAMESPACE")
    temporal_task_queue: str = Field(default="somna-agent-core", alias="TEMPORAL_TASK_QUEUE")

    # Anthropic SDK base_url（默认留空 = LITELLM 根地址 + /v1/messages，可走 agent-* 别名）。
    # 若填 URL，须为 LiteLLM 根（如 http://litellm:4000）；勿填 .../anthropic（该路径直通官方 API）。
    anthropic_base_url: str = Field(default="", alias="ANTHROPIC_BASE_URL")
    anthropic_auth_token: str = Field(default="", alias="ANTHROPIC_AUTH_TOKEN")

    # ---- Agent model aliases ----
    agent_default_planner: str = Field(default="agent-planner", alias="AGENT_DEFAULT_PLANNER")
    agent_default_taskframe: str = Field(default="agent-taskframe", alias="AGENT_DEFAULT_TASKFRAME")
    agent_default_executor: str = Field(default="agent-executor", alias="AGENT_DEFAULT_EXECUTOR")
    agent_default_coder: str = Field(default="agent-coder", alias="AGENT_DEFAULT_CODER")
    agent_default_reasoner: str = Field(default="agent-reasoner", alias="AGENT_DEFAULT_REASONER")
    agent_default_longctx: str = Field(default="agent-longctx", alias="AGENT_DEFAULT_LONGCTX")
    agent_default_skill: str = Field(default="agent-skill", alias="AGENT_DEFAULT_SKILL")
    agent_default_visual_critique: str = Field(
        default="qwen3-vl-plus",
        alias="AGENT_DEFAULT_VISUAL_CRITIQUE",
    )
    agent_default_mcp_wan_t2i: str = Field(default="wan2.2-t2i-flash", alias="AGENT_DEFAULT_MCP_WAN_T2I")
    agent_default_mcp_wan_t2v: str = Field(default="wan2.2-t2v-plus", alias="AGENT_DEFAULT_MCP_WAN_T2V")
    agent_default_mcp_wan_i2v: str = Field(default="happyhorse-1.0-i2v", alias="AGENT_DEFAULT_MCP_WAN_I2V")
    agent_default_mcp_wan_r2v: str = Field(default="wan2.7-r2v", alias="AGENT_DEFAULT_MCP_WAN_R2V")
    agent_default_mcp_wan_video_edit: str = Field(
        default="wan2.7-videoedit",
        alias="AGENT_DEFAULT_MCP_WAN_VIDEO_EDIT",
    )
    agent_default_mcp_minimax_tts: str = Field(default="speech-2.6-hd", alias="AGENT_DEFAULT_MCP_MINIMAX_TTS")
    agent_compact_model: str = Field(default="agent-cheap", alias="AGENT_COMPACT_MODEL")
    agent_embed_model: str = Field(default="agent-embed", alias="AGENT_EMBED_MODEL")
    agent_max_turns: int = Field(default=40, alias="AGENT_MAX_TURNS")
    agent_compact_token_threshold: int = Field(default=80000, alias="AGENT_COMPACT_TOKEN_THRESHOLD")

    # ---- Langfuse (optional in dev) ----
    langfuse_host: str = Field(default="", alias="LANGFUSE_HOST")
    langfuse_public_key: str = Field(default="", alias="LANGFUSE_PUBLIC_KEY")
    langfuse_secret_key: str = Field(default="", alias="LANGFUSE_SECRET_KEY")

    # ---- S3 ----
    s3_endpoint: str = Field(default="", alias="S3_ENDPOINT")
    s3_bucket: str = Field(default="somna", alias="S3_BUCKET")
    s3_access_key: str = Field(default="", alias="S3_ACCESS_KEY")
    s3_secret_key: str = Field(default="", alias="S3_SECRET_KEY")

    # 会话附件上传上限（与 MCP Hub MCP_FS_MAX_FILE_BYTES 默认一致，均为 256MiB；若调大须两边一起调）
    attachment_max_bytes: int = Field(
        default=256 * 1024 * 1024,
        alias="ATTACHMENT_MAX_BYTES",
    )

    # ---- MCP Hub ----
    mcp_hub_url: str = Field(default="http://mcp-hub:8090", alias="MCP_HUB_URL")
    # Empty = emit same-origin /api/v1/... artifact URLs (Next rewrites to agent-core).
    public_api_base: str = Field(default="", alias="NEXT_PUBLIC_API_BASE")

    # ---- Memory (mem0) ----
    memory_enabled: bool = Field(default=False, alias="MEMORY_ENABLED")
    memory_top_k: int = Field(default=5, alias="MEMORY_TOP_K")
    mem0_llm_provider: str = Field(default="litellm", alias="MEM0_LLM_PROVIDER")
    mem0_llm_base_url: str = Field(default="http://litellm:4000", alias="MEM0_LLM_BASE_URL")
    mem0_llm_model: str = Field(default="agent-cheap", alias="MEM0_LLM_MODEL")
    mem0_embedder_model: str = Field(default="agent-embed", alias="MEM0_EMBEDDER_MODEL")
    mem0_vector_store: str = Field(default="milvus", alias="MEM0_VECTOR_STORE")
    mem0_collection: str = Field(default="mem0_memories", alias="MEM0_COLLECTION")
    milvus_host: str = Field(default="milvus", alias="MILVUS_HOST")
    milvus_port: int = Field(default=19530, alias="MILVUS_PORT")

    # ---- Auth (JWT) ----
    jwt_secret: str = Field(
        default="dev-insecure-change-me",
        alias="JWT_SECRET",
        description="HS256 secret for access tokens; MUST override in production",
    )
    jwt_expire_hours: int = Field(default=168, alias="JWT_EXPIRE_HOURS")

    # ---- Email (SMTP / 验证码) ----
    smtp_host: str = Field(default="", alias="SMTP_HOST")
    smtp_port: int = Field(default=465, alias="SMTP_PORT")
    smtp_user: str = Field(default="", alias="SMTP_USER")
    smtp_password: str = Field(default="", alias="SMTP_PASSWORD")
    smtp_from: str = Field(default="", alias="SMTP_FROM")
    smtp_use_tls: bool = Field(default=True, alias="SMTP_USE_TLS")

    # Google Sign-In：验证前端 GIS 下发的 id_token（audience = 该 Client ID）
    google_oauth_client_id: str = Field(default="", alias="GOOGLE_OAUTH_CLIENT_ID")

    # 逗号分隔：首次注册/谷歌建号时把这些邮箱设为 admin，其它为 user
    admin_emails: str = Field(default="", alias="ADMIN_EMAILS")

    # ---- Misc ----
    prompts_dir: str = Field(default="/packages/prompts", alias="PROMPTS_DIR")


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
