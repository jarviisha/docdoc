# Shortcuts for the commands this repository already documents. Every variable
# can be overridden: `make api DB=postgresql://…`, or export the DOCDOC_* name.

DB      ?= postgresql://docdoc:docdoc@127.0.0.1:55432/docdoc_demo
TEST_DB ?= postgresql://docdoc:docdoc@127.0.0.1:55432/docdoc
STORE   ?= /tmp/docdoc-console-store
TENANT  ?= acme
PORT    ?= 8000
KIND    ?= ocr
ADAPTER ?= echo
PG      ?= docdoc-dev-pg
RUN_API    = uv run python -m uvicorn --factory docdoc.api.app:create_app --port $(PORT)
RUN_WORKER = uv run docdoc worker --kind $(KIND) --adapter $(ADAPTER)
COMPOSE := docker compose -f packaging/docker/compose.yml

export DOCDOC_RUN_DATABASE_URL ?= $(DB)
export DOCDOC_STORE_ROOT ?= $(STORE)
# The demo database pins its unprefixed store root to this tenant; a mismatch is refused.
export DOCDOC_DEFAULT_TENANT ?= $(TENANT)
# The OCR hosts' endpoints file, when it is where it has been kept so far.
ifneq ($(wildcard /tmp/docdoc-ocr-endpoints.json),)
export DOCDOC_OCR_ENDPOINTS_FILE ?= /tmp/docdoc-ocr-endpoints.json
endif

.DEFAULT_GOAL := help
.PHONY: help install test lint fmt ui check dev migrate api worker user users up down

help: ## list the targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-9s %s\n", $$1, $$2}'

install: ## Python deps (all extras) and the console's npm deps
	uv sync --all-extras --frozen
	cd ui && npm ci

test: ## the Python suite; Postgres tests use TEST_DB (never the demo database)
	env -u DOCDOC_RUN_DATABASE_URL -u DOCDOC_STORE_ROOT -u DOCDOC_DEFAULT_TENANT -u DOCDOC_OCR_ENDPOINTS_FILE \
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

dev: ## Postgres container PG up, migrate, then API + OCR worker + Vite (http://localhost:5173/console/, proxies /v1 to :8000); Ctrl-C stops all
	docker start $(PG) >/dev/null
	@for i in 1 2 3 4 5 6 7 8 9 10; do out=$$(uv run docdoc migrate 2>&1) && exit 0; sleep 1; done; \
	 echo "$$out"; exit 1
	@trap 'kill 0' INT TERM EXIT; \
	 $(RUN_API) & $(RUN_WORKER) & (cd ui && npx vite) & wait

migrate: ## apply the run-state schema to DB
	uv run docdoc migrate

api: ## serve the API and the built console on PORT (build it with `make ui`)
	$(RUN_API)

worker: ## one worker of KIND (ocr|extraction); OCR needs DOCDOC_OCR_ENDPOINTS_FILE
	$(RUN_WORKER)

user: ## add a console account: make user NAME=admin (prompts for the password)
	@test -n "$(NAME)" || { echo "usage: make user NAME=<username>"; exit 2; }
	uv run docdoc console-user add $(NAME)

users: ## list console accounts
	uv run docdoc console-user list

up: ## the docker compose topology (api, workers, postgres, object store); its migrate service applies the schema
	$(COMPOSE) up -d --build

down: ## stop the compose topology, keeping its data
	$(COMPOSE) down
