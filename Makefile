# Shortcuts for the commands this repository already documents. Every variable
# can be overridden: `make api DB=postgresql://…`, or export the DOCDOC_* name.

DB      ?= postgresql://docdoc:docdoc@127.0.0.1:55432/docdoc_demo
TEST_DB ?= postgresql://docdoc:docdoc@127.0.0.1:55432/docdoc
STORE   ?= /tmp/docdoc-console-store
PORT    ?= 8000
KIND    ?= ocr
ADAPTER ?= echo
COMPOSE := docker compose -f packaging/docker/compose.yml

export DOCDOC_RUN_DATABASE_URL ?= $(DB)
export DOCDOC_STORE_ROOT ?= $(STORE)

.DEFAULT_GOAL := help
.PHONY: help install test lint fmt ui check migrate api worker user users up down

help: ## list the targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-9s %s\n", $$1, $$2}'

install: ## Python deps (all extras) and the console's npm deps
	uv sync --all-extras --frozen
	cd ui && npm ci

test: ## the Python suite; Postgres tests use TEST_DB (never the demo database)
	DOCDOC_TEST_DATABASE_URL=$(TEST_DB) uv run pytest -m "not perf"

lint: ## ruff, format check, mypy strict, import boundaries
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy src/docdoc
	uv run lint-imports

fmt: ## format and auto-fix Python
	uv run ruff format .
	uv run ruff check --fix .

ui: ## console: typecheck, tests, boundaries, licences, build
	cd ui && npx tsc --noEmit && npm test && npm run lint:boundaries && npm run licenses && npm run build

check: lint test ui ## everything CI checks, locally

migrate: ## apply the run-state schema to DB
	uv run docdoc migrate

api: ## serve the API and the built console on PORT (build it with `make ui`)
	uv run python -m uvicorn --factory docdoc.api.app:create_app --port $(PORT)

worker: ## one worker of KIND (ocr|extraction); OCR needs DOCDOC_OCR_ENDPOINTS_FILE
	uv run docdoc --adapter $(ADAPTER) worker --kind $(KIND)

user: ## add a console account: make user NAME=admin (prompts for the password)
	@test -n "$(NAME)" || { echo "usage: make user NAME=<username>"; exit 2; }
	uv run docdoc console-user add $(NAME)

users: ## list console accounts
	uv run docdoc console-user list

up: ## the docker compose topology (api, workers, postgres, object store), migrated
	$(COMPOSE) up -d --build
	$(COMPOSE) run --rm api docdoc migrate

down: ## stop the compose topology, keeping its data
	$(COMPOSE) down
