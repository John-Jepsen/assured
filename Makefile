# Insurance AI — developer commands.
# Local (host) targets assume a Python venv at .venv and Node for the web app.
# Docker targets need only Docker + Docker Compose.

API_DIR := apps/api
WEB_DIR := apps/web
VENV := .venv
PY := $(VENV)/bin/python
PIP := $(VENV)/bin/pip
# Local dev DB (override to point at your Postgres). Falls back to a SQLite file.
export INSURANCE_AI_DATABASE_URL ?= postgresql+asyncpg://$(USER)@localhost:5432/insurance_ai

.PHONY: help setup setup-web seed dev dev-web test eval lint down up build clean

help:
	@echo "Targets:"
	@echo "  setup      Create Python venv + install API (dev extras)"
	@echo "  setup-web  Install frontend dependencies"
	@echo "  seed       Create schema, seed synthetic data, ingest knowledge base"
	@echo "  dev        Run the API (uvicorn, reload) on :8000"
	@echo "  dev-web    Run the Vite dev server on :5173"
	@echo "  test       Run the pytest suite (unit + API + evals)"
	@echo "  eval       Run the YAML evaluation suite (make eval)"
	@echo "  up         docker compose up --build (full stack)"
	@echo "  down       docker compose down"
	@echo "  clean      Remove venv, caches, local test DBs"

setup:
	python3 -m venv $(VENV)
	$(PIP) install --upgrade pip
	cd $(API_DIR) && ../../$(PIP) install -e ".[dev]"

setup-web:
	cd $(WEB_DIR) && npm install

seed:
	cd $(API_DIR) && ../../$(PY) -m scripts.bootstrap

dev:
	cd $(API_DIR) && ../../$(VENV)/bin/uvicorn insurance_ai.main:app --reload --port 8000

dev-web:
	cd $(WEB_DIR) && npm run dev

test:
	$(PY) -m pytest tests/ -q

eval:
	cd $(API_DIR) && ../../$(PY) -m insurance_ai.evals.runner

lint:
	$(VENV)/bin/ruff check $(API_DIR)/insurance_ai || true

up:
	docker compose up --build

down:
	docker compose down

clean:
	rm -rf $(VENV) .pytest_cache .ruff_cache tests/.api_test.db
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
