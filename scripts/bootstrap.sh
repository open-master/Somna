#!/usr/bin/env bash
# ============================================================
#  Somna AI — bootstrap.sh
#  首次启动环境准备：.env / 必要目录 / Docker 网络
# ============================================================
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

say() { printf "\033[1;36m[bootstrap]\033[0m %s\n" "$*"; }
warn() { printf "\033[1;33m[warn]\033[0m %s\n" "$*"; }
err()  { printf "\033[1;31m[err]\033[0m %s\n" "$*"; exit 1; }

say "Checking prerequisites..."
command -v docker >/dev/null || err "Docker is required but not installed."
docker info >/dev/null 2>&1 || err "Docker daemon is not running."
docker compose version >/dev/null 2>&1 || err "Docker Compose v2 is required."

if [ ! -f ".env" ]; then
    say "Creating .env from .env.example"
    cp .env.example .env
    warn "请打开 .env 填入各家模型的 API KEY (MOONSHOT / DASHSCOPE / DEEPSEEK / SILICONFLOW)"
else
    say ".env already exists — skip"
fi

python3 scripts/ensure_mcp_hub_token.py

say "Creating external docker network for sandboxes..."
docker network inspect somna_sandbox_net >/dev/null 2>&1 \
    || docker network create somna_sandbox_net >/dev/null

say "Pulling core images..."
docker compose pull --ignore-buildable 2>/dev/null || true

cat <<'EOF'

✔ Bootstrap complete.

下一步:
  1. 编辑 .env，至少填写 MOONSHOT_API_KEY / DASHSCOPE_API_KEY / DEEPSEEK_API_KEY / SILICONFLOW_API_KEY
  2. make up           # 启动全栈
  3. make health       # 健康检查
  4. make logs s=litellm   # 观察 LiteLLM 日志

EOF
