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

    fs_max_file_bytes: int = Field(default=50 * 1024 * 1024, alias="MCP_FS_MAX_FILE_BYTES")  # 50 MiB
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


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
