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
check "minio"              "curl -fsS http://localhost:9000/minio/health/live"
check "milvus"             "curl -fsS http://localhost:9091/healthz"
check "nats"               "curl -fsS http://localhost:8222/healthz"
check "temporal"           "docker compose exec -T temporal tctl --address temporal:7233 cluster health"
check "temporal-ui"        "curl -fsS -o /dev/null -w '%{http_code}' http://localhost:8233 | grep -qE '200|301|302'"
check "litellm"            "curl -fsS http://localhost:4000/health/liveliness"
check "langfuse"           "curl -fsS http://localhost:3001/api/public/health"
check "mcp-hub"            "curl -fsS http://localhost:8090/healthz"
check "agent-core"         "curl -fsS http://localhost:8000/healthz"
check "web"                "curl -fsS http://localhost:3000/api/health"
echo "=========================================================="
echo " Passed: $PASS    Failed: $FAIL"
[ "$FAIL" -eq 0 ] && exit 0 || exit 1
