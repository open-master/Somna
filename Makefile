.PHONY: help up down restart logs ps health clean reset seed bootstrap env test-agent-core-docker

SHELL := /bin/bash
COMPOSE := docker compose

help: ## 显示可用命令
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-14s\033[0m %s\n", $$1, $$2}'

env: ## 初始化 .env（从 .env.example 复制，不覆盖已有文件）
	@if [ ! -f .env ]; then cp .env.example .env && echo "✓ .env created"; else echo "✓ .env already exists"; fi

bootstrap: env ## 首次准备：创建 .env、网络、数据卷
	@bash scripts/bootstrap.sh

up: env ## 启动全栈
	$(COMPOSE) up -d
	@echo ""
	@echo "============================================"
	@echo " Somna AI stack is up. Service endpoints:"
	@echo "============================================"
	@echo "  Web (frontend)     http://localhost:3000"
	@echo "  BFF                http://localhost:8080"
	@echo "  Agent Core         http://localhost:8000"
	@echo "  LiteLLM            http://localhost:4000"
	@echo "  Temporal UI        http://localhost:8233"
	@echo "  Langfuse           http://localhost:3001"
	@echo "  MinIO Console      http://localhost:9001"
	@echo "  Postgres           localhost:5432"
	@echo "  MySQL              localhost:(MYSQL_HOST_PORT，默认 3306)"
	@echo "  Milvus             localhost:19530"
	@echo "  Redis              localhost:6379"
	@echo "  NATS               localhost:4222"
	@echo "============================================"

down: ## 停止全栈（保留数据卷）
	$(COMPOSE) down

restart: ## 重启全栈
	$(COMPOSE) restart

ps: ## 查看服务状态
	$(COMPOSE) ps

logs: ## 跟随日志 (使用 make logs s=agent-core 指定服务)
	@if [ -n "$(s)" ]; then $(COMPOSE) logs -f --tail=200 $(s); else $(COMPOSE) logs -f --tail=100; fi

health: ## 健康检查
	@bash scripts/health_check.sh

clean: ## 停止并删除容器（保留数据卷）
	$(COMPOSE) down --remove-orphans

reset: ## 危险：清空所有数据卷和容器
	@read -p "⚠️  这会清空所有数据卷，确认? [y/N] " ans && [ "$$ans" = "y" ] || exit 1
	$(COMPOSE) down -v --remove-orphans

seed: ## 初始化种子数据
	$(COMPOSE) exec agent-core python -m app.scripts.seed

test-agent-core-docker: ## 在 agent-core 容器内跑 tests/（挂载本机 apps/agent-core/tests，需 --user root 供 uv 写锁）
	@$(COMPOSE) run --rm --no-deps --user root \
		-v "$$(pwd)/apps/agent-core/tests:/app/tests:ro" \
		agent-core \
		sh -lc 'uv pip install --system --no-cache-dir pytest pytest-asyncio respx >/dev/null && cd /app && python -m pytest tests/ -q --tb=short'
