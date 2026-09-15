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
# Deliberately NOT passing --timeout-graceful-shutdown. Leaving it unset makes
# uvicorn's shutdown wait_for never fire, so it never cancels in-flight ASGI tasks
# (uvicorn server.py: on timeout it calls t.cancel() on every server task). The chat
# and telephony WebSocket handlers run their database work inline, so such a cancel
# could land inside a statement and abandon a pooled connection with an open
# transaction -- on Postgres, one holding row locks until GC, across every replica of
# a rolling deploy at once. Before adding this flag, wrap prepare_message and
# finish_message in routes_chat.py and routes_telephony.py with
# insurance_ai.db.cancellation.uninterruptible, the way routes_voice.py already does.
exec uvicorn insurance_ai.main:app \
    --host "${INSURANCE_AI_API_HOST:-0.0.0.0}" \
    --port "${INSURANCE_AI_API_PORT:-8000}" \
    --workers "${UVICORN_WORKERS:-1}" \
    --no-proxy-headers
