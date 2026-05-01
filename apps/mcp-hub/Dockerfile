# syntax=docker/dockerfile:1.7
# ============================================================
#  Somna AI — MCP Hub
#  Python 3.12 slim + uv
# ============================================================
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_LINK_MODE=copy

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates build-essential git nodejs npm \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv==0.4.25

WORKDIR /app

COPY apps/mcp-hub/pyproject.toml /app/pyproject.toml
RUN uv pip install --system --no-cache-dir -r /app/pyproject.toml \
    && uv pip install --system --no-cache-dir pytest pytest-asyncio

COPY apps/mcp-hub/app   /app/app
COPY apps/mcp-hub/tests /app/tests

# Sandbox root is mounted from compose as a named volume
RUN mkdir -p /var/somna/sandboxes
ENV SANDBOX_ROOT=/var/somna/sandboxes

EXPOSE 8090

HEALTHCHECK --interval=20s --timeout=5s --start-period=10s --retries=5 \
    CMD curl -fsS http://localhost:8090/healthz || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8090", "--workers", "1", "--log-level", "info"]
