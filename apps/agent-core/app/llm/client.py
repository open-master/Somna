"""LLM client facade — all model calls go through LiteLLM.

Business code uses *aliases* (agent-planner / agent-taskframe / agent-executor / agent-coder / agent-cheap
/ agent-embed / agent-rerank) defined in infra/litellm/config.yaml. Changing underlying
models only touches LiteLLM config, never Python code.
"""

from __future__ import annotations

from functools import lru_cache

import httpx
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from openai import AsyncOpenAI

from app.config import get_settings

# LiteLLM 重启/迁移窗口内可能出现短暂拒连；拉长 connect/read 并提高 SDK 级重试。
_LLM_HTTP_TIMEOUT = httpx.Timeout(connect=30.0, read=300.0, write=60.0, pool=30.0)
_LLM_MAX_RETRIES = 10


@lru_cache
def _openai_base_url() -> str:
    return f"{get_settings().litellm_url.rstrip('/')}/v1"


@lru_cache
def _openai_api_key() -> str:
    return get_settings().litellm_master_key


@lru_cache
def _anthropic_base_url() -> str:
    s = get_settings()
    custom = (s.anthropic_base_url or "").strip().rstrip("/")
    litellm_root = s.litellm_url.rstrip("/")
    if custom:
        # 旧模板误配 `.../anthropic` 会走 LiteLLM 官方 API 直通，agent-* 别名不生效 → 401
        if custom.endswith("/anthropic"):
            custom = custom[: -len("/anthropic")].rstrip("/") or litellm_root
        return custom
    # LiteLLM：Anthropic SDK 默认请求 `{base_url}/v1/messages`。必须用网关根 URL，才会走
    # 统一路由与 model_group_alias（与 /v1/chat/completions 一致）。
    # `{litellm}/anthropic/v1/messages` 是官方 API 直通（passthrough），会打到 api.anthropic.com，且无别名解析。
    return s.litellm_url.rstrip("/")


@lru_cache
def _anthropic_api_key() -> str:
    s = get_settings()
    return (s.anthropic_auth_token or "").strip() or s.litellm_master_key


def anthropic_subprocess_env() -> dict[str, str]:
    """LiteLLM 根 URL + key，供 Claude Agent SDK（子进程 CLI）走统一别名路由。"""
    return {
        "ANTHROPIC_API_KEY": _anthropic_api_key(),
        "ANTHROPIC_BASE_URL": _anthropic_base_url(),
    }


@lru_cache
def get_async_openai() -> AsyncOpenAI:
    """Raw AsyncOpenAI client pointed at LiteLLM. Use for streaming chat completions."""
    return AsyncOpenAI(
        base_url=_openai_base_url(),
        api_key=_openai_api_key(),
        timeout=_LLM_HTTP_TIMEOUT,
        max_retries=_LLM_MAX_RETRIES,
    )


def get_chat(model_alias: str, temperature: float = 0.3, **kwargs) -> ChatOpenAI:
    """LangChain ChatOpenAI pointed at LiteLLM. Use for deterministic tasks / tool calling."""
    return ChatOpenAI(
        model=model_alias,
        base_url=_openai_base_url(),
        api_key=_openai_api_key(),
        temperature=temperature,
        request_timeout=_LLM_HTTP_TIMEOUT,
        max_retries=_LLM_MAX_RETRIES,
        streaming=kwargs.pop("streaming", False),
        **kwargs,
    )


def get_embeddings(model_alias: str | None = None) -> OpenAIEmbeddings:
    alias = model_alias or get_settings().agent_embed_model
    return OpenAIEmbeddings(
        model=alias,
        base_url=_openai_base_url(),
        api_key=_openai_api_key(),
        request_timeout=_LLM_HTTP_TIMEOUT,
        max_retries=_LLM_MAX_RETRIES,
    )
