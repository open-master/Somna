"""MCP Hub settings loaded from environment / .env."""

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
    service_name: str = "somna-mcp-hub"

    # ---- Sandbox root ----
    sandbox_root: str = Field(default="/var/somna/sandboxes", alias="SANDBOX_ROOT")

    # ---- Tool limits ----
    shell_default_timeout_sec: int = Field(default=60, alias="MCP_SHELL_TIMEOUT_SEC")
    shell_max_timeout_sec: int = Field(default=600, alias="MCP_SHELL_MAX_TIMEOUT_SEC")
    shell_output_preview_bytes: int = Field(default=4096, alias="MCP_SHELL_PREVIEW_BYTES")

    fs_max_file_bytes: int = Field(
        default=256 * 1024 * 1024,
        alias="MCP_FS_MAX_FILE_BYTES",
    )  # 256 MiB：视频等交付物预览需一次 read；过大会占内存，可用 env 再调
    fs_max_list_entries: int = Field(default=2000, alias="MCP_FS_MAX_LIST_ENTRIES")

    # ---- Search tool ----
    # provider: brave | duckduckgo | tavily | serper | mock
    search_provider: str = Field(default="duckduckgo", alias="MCP_SEARCH_PROVIDER")
    # When DDG/API fails: if false (default), return error instead of fake [MOCK] results.
    search_http_error_fallback_mock: bool = Field(
        default=False,
        alias="MCP_SEARCH_HTTP_ERROR_FALLBACK_MOCK",
    )
    tavily_api_key: str = Field(default="", alias="TAVILY_API_KEY")
    serper_api_key: str = Field(default="", alias="SERPER_API_KEY")
    brave_search_api_key: str = Field(default="", alias="BRAVE_SEARCH_API_KEY")
    brave_request_timeout_sec: float = Field(default=60.0, alias="MCP_BRAVE_TIMEOUT_SEC")
    jina_api_key: str = Field(default="", alias="JINA_API_KEY")

    # ---- DashScope / 万相（与 Qwen 共用百炼 DASHSCOPE_API_KEY；HTTP 前缀须与 Key 地域一致）----
    dashscope_api_key: str = Field(default="", alias="DASHSCOPE_API_KEY")
    dashscope_http_base: str = Field(
        default="https://dashscope.aliyuncs.com/api/v1",
        alias="MCP_DASHSCOPE_HTTP_BASE",
    )
    wan_t2i_model: str = Field(default="wan2.2-t2i-flash", alias="MCP_WAN_T2I_MODEL")
    wan_t2v_model: str = Field(default="wan2.2-t2v-plus", alias="MCP_WAN_T2V_MODEL")
    wan_poll_interval_sec: float = Field(default=3.0, alias="MCP_WAN_POLL_INTERVAL_SEC")
    wan_poll_timeout_sec: float = Field(default=600.0, alias="MCP_WAN_POLL_TIMEOUT_SEC")
    wan_request_timeout_sec: float = Field(default=120.0, alias="MCP_WAN_REQUEST_TIMEOUT_SEC")

    # ---- Visual critique (DashScope OpenAI-compatible multimodal) ----
    visual_critique_model: str = Field(default="qwen3-vl-plus", alias="MCP_VISUAL_CRITIQUE_MODEL")
    visual_critique_openai_base: str = Field(
        default="https://dashscope.aliyuncs.com/compatible-mode/v1",
        alias="MCP_VISUAL_CRITIQUE_OPENAI_BASE",
    )
    visual_critique_timeout_sec: float = Field(default=120.0, alias="MCP_VISUAL_CRITIQUE_TIMEOUT_SEC")
    visual_critique_max_image_bytes: int = Field(
        default=20 * 1024 * 1024,
        alias="MCP_VISUAL_CRITIQUE_MAX_IMAGE_BYTES",
    )

    # ---- MiniMax TTS（国内常用 https://api.minimaxi.com；国际为 https://api.minimax.io）----
    minimax_api_key: str = Field(default="", alias="MINIMAX_API_KEY")
    minimax_http_base: str = Field(
        default="https://api.minimaxi.com",
        alias="MCP_MINIMAX_BASE_URL",
    )
    minimax_tts_model: str = Field(default="speech-2.6-hd", alias="MCP_MINIMAX_TTS_MODEL")
    minimax_tts_voice_id: str = Field(default="male-qn-qingse", alias="MCP_MINIMAX_TTS_VOICE_ID")
    minimax_request_timeout_sec: float = Field(default=120.0, alias="MCP_MINIMAX_REQUEST_TIMEOUT_SEC")


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
