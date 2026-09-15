#!/usr/bin/env bash
# API container entrypoint. Database mutations are intentionally separate one-shot
# deployment jobs; ordinary API replicas only serve traffic.
set -euo pipefail

if [[ "$#" -gt 0 ]]; then
    exec "$@"
fi

echo "[entrypoint] starting uvicorn…"
# --no-proxy-headers: uvicorn must not rewrite request.client from forwarded headers.
# Proxy trust is a single explicit application decision made against
# INSURANCE_AI_RATE_LIMIT_TRUSTED_PROXIES, so the ASGI peer address stays the raw,
# unforgeable one the rate limiter measures that decision against.
exec uvicorn insurance_ai.main:app \
    --host "${INSURANCE_AI_API_HOST:-0.0.0.0}" \
    --port "${INSURANCE_AI_API_PORT:-8000}" \
    --workers "${UVICORN_WORKERS:-1}" \
    --no-proxy-headers
