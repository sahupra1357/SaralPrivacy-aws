# SaralPrivacy — local stack. Every target is a thin docker compose wrapper.
# Tests run ONLY via `make test*` (see CLAUDE.md "Token economy").

COMPOSE ?= docker compose
MODULE ?=

.PHONY: help up watch down clean ps logs sh-api sh-web migrate revision client lint \
        test test-backend test-frontend

help:
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-16s\033[0m %s\n",$$1,$$2}'

up: ## start everything locally: db, backend, worker, frontend, mailcatcher, minio (health-checked)
	$(COMPOSE) up -d --wait
	@echo "site      http://localhost:3000        admin  http://localhost:3000/admin/login"
	@echo "api docs  http://localhost:8000/docs   inbox  http://localhost:1080   storage http://localhost:9001"

watch: ## start with hot reload
	$(COMPOSE) watch

down: ## stop everything
	$(COMPOSE) down

clean: ## stop and drop volumes (database, minio)
	$(COMPOSE) down -v --remove-orphans

ps: ## container status
	$(COMPOSE) ps

logs: ## follow backend + frontend + worker logs
	$(COMPOSE) logs -f backend worker frontend

sh-api: ## shell in the backend container
	$(COMPOSE) exec backend bash

sh-web: ## shell in the frontend container
	$(COMPOSE) exec frontend sh

migrate: ## alembic upgrade head
	$(COMPOSE) run --rm --no-deps -e POSTGRES_SERVER=db backend alembic upgrade head

revision: ## autogenerate a migration: make revision M="forms module"  (orchestrator only)
	@test -n "$(M)" || (echo 'usage: make revision M="message"' && exit 1)
	$(COMPOSE) run --rm --no-deps -e POSTGRES_SERVER=db backend alembic revision --autogenerate -m "$(M)"

client: ## regenerate frontend/src/client from the backend OpenAPI schema (needs Node on the host)
	$(COMPOSE) run --rm --no-deps -T backend python -c "import json, app.main as m; print(json.dumps(m.app.openapi()))" > frontend/openapi.json
	cd frontend && npm run generate-client

lint: ## static checks only: ruff, mypy, tsc
	$(COMPOSE) run --rm --no-deps backend bash scripts/lint.sh
	$(COMPOSE) run --rm --no-deps frontend npm run typecheck

test: ## full suite: backend pytest + frontend vitest + node:test
	$(COMPOSE) up -d --wait db
	$(COMPOSE) run --rm -e POSTGRES_SERVER=db backend bash scripts/test.sh
	$(COMPOSE) run --rm --no-deps frontend npm run test:unit
	$(COMPOSE) run --rm --no-deps frontend npm run test:lib

test-backend: ## one module: make test-backend MODULE=forms
	$(COMPOSE) up -d --wait db
	$(COMPOSE) run --rm -e POSTGRES_SERVER=db backend bash scripts/test.sh app/tests/$(MODULE)

test-frontend: ## one module: make test-frontend MODULE=forms
	$(COMPOSE) run --rm --no-deps frontend npx vitest run tests/unit/$(MODULE)


