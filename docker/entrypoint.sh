#!/usr/bin/env bash
# API container entrypoint: apply schema + seed (idempotent) then serve.
# Dependency readiness is guaranteed by compose (depends_on: service_healthy),
# so we do not sleep-wait; we still fail loudly if the DB is unreachable.
set -euo pipefail

echo "[entrypoint] running migrations (alembic upgrade head)…"
alembic upgrade head

echo "[entrypoint] seeding synthetic data + ingesting knowledge (if empty)…"
python -m scripts.bootstrap --if-empty

echo "[entrypoint] starting uvicorn…"
exec uvicorn insurance_ai.main:app \
    --host "${INSURANCE_AI_API_HOST:-0.0.0.0}" \
    --port "${INSURANCE_AI_API_PORT:-8000}" \
    --workers "${UVICORN_WORKERS:-1}"
