#!/usr/bin/env bash
# ============================================================
#  Somna AI — health_check.sh
# ============================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PASS=0
FAIL=0

check() {
    local name="$1"; local cmd="$2"
    if eval "$cmd" >/dev/null 2>&1; then
        printf "  \033[1;32m✔\033[0m %-22s %s\n" "$name" "OK"
        PASS=$((PASS+1))
    else
        printf "  \033[1;31m✘\033[0m %-22s %s\n" "$name" "FAIL"
        FAIL=$((FAIL+1))
    fi
}

echo "==================== Somna AI Health ===================="
check "docker daemon"      "docker info"
check "postgres"           "docker compose exec -T postgres pg_isready -U somna"
check "mysql"              "docker compose exec -T mysql mysqladmin ping -h 127.0.0.1 -uroot -p\$MYSQL_ROOT_PASSWORD --silent"
check "redis"              "docker compose exec -T redis redis-cli ping | grep -q PONG"
check "minio"              "docker compose exec -T minio curl -fsS http://localhost:9000/minio/health/live"
check "milvus"             "docker compose exec -T milvus curl -fsS http://localhost:9091/healthz"
check "nats"               "docker compose exec -T nats wget -qO- http://localhost:8222/healthz"
check "temporal"           "docker compose exec -T temporal tctl --address temporal:7233 cluster health"
check "temporal-ui"        "docker compose exec -T agent-core curl -fsS -o /dev/null -w '%{http_code}' http://temporal-ui:8080 | grep -qE '200|301|302'"
check "litellm"            "docker compose exec -T litellm python -c \"import urllib.request; urllib.request.urlopen('http://127.0.0.1:4000/health/liveliness', timeout=9)\""
check "langfuse"           "docker compose exec -T langfuse wget -qO- http://localhost:3000/api/public/health"
check "mcp-hub"            "docker compose exec -T mcp-hub curl -fsS http://localhost:8090/healthz"
check "agent-core"         "docker compose exec -T agent-core curl -fsS http://localhost:8000/healthz"
check "web"                "docker compose exec -T web node -e \"fetch('http://localhost:3000/api/health').then((r)=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))\""
echo "=========================================================="
echo " Passed: $PASS    Failed: $FAIL"
[ "$FAIL" -eq 0 ] && exit 0 || exit 1
