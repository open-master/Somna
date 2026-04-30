# syntax=docker/dockerfile:1.7
# ============================================================
#  Somna AI — Agent Core
#  Python 3.12 slim + uv (fast installer)
# ============================================================
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_LINK_MODE=copy

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates build-essential libpq-dev git \
    && rm -rf /var/lib/apt/lists/*

# Install uv
RUN pip install --no-cache-dir uv==0.4.25

WORKDIR /app

# --- Copy shared local packages first (for better layer caching) ---
COPY packages/event-schema/python /packages/event-schema/python

# --- Copy project manifests ---
COPY apps/agent-core/pyproject.toml /app/pyproject.toml

# Install shared package first, then project deps. --no-sources keeps uv from
# re-resolving the `[tool.uv.sources]` relative path (which only exists in the
# monorepo layout, not inside the container).
RUN uv pip install --system --no-cache-dir -e /packages/event-schema/python \
    && uv pip install --system --no-cache-dir --no-sources -r /app/pyproject.toml

# --- Copy source ---
COPY apps/agent-core/app   /app/app
COPY packages/prompts      /packages/prompts

# Claude Agent SDK 子进程 CLI：bypassPermissions 即「跳过权限」类选项，禁止在 root 下运行。
RUN groupadd --gid 1000 app \
    && useradd --uid 1000 --gid app --home-dir /app --no-create-home app \
    && mkdir -p /app/.cache \
    && chown -R app:app /app /packages

USER app

ENV PROMPTS_DIR=/packages/prompts \
    HOME=/app

EXPOSE 8000

HEALTHCHECK --interval=20s --timeout=5s --start-period=20s --retries=5 \
    CMD curl -fsS http://localhost:8000/healthz || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--log-level", "info"]
