"""ASGI request correlation, access logging, and HTTP RED metrics."""

from __future__ import annotations

import re
import time
import uuid

from structlog.contextvars import bind_contextvars, clear_contextvars

from insurance_ai.observability.metrics import (
    HTTP_REQUEST_DURATION,
    HTTP_REQUESTS,
    HTTP_REQUESTS_ACTIVE,
)

_REQUEST_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


def request_id_from_scope(scope: dict) -> str:
    """Return the validated ID installed by :class:`ObservabilityMiddleware`."""
    return scope.get("request_id", "unknown")


class ObservabilityMiddleware:
    """Add one safe correlation ID and request metrics around each ASGI request."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers", ()))
        supplied = headers.get(b"x-request-id", b"").decode("ascii", errors="ignore")
        request_id = supplied if _REQUEST_ID.fullmatch(supplied) else str(uuid.uuid4())
        scope["request_id"] = request_id
        clear_contextvars()
        bind_contextvars(request_id=request_id)

        if scope["type"] == "websocket":
            try:
                await self.app(scope, receive, send)
            finally:
                clear_contextvars()
            return

        from insurance_ai.observability import get_logger

        log = get_logger("http")
        method = scope.get("method", "UNKNOWN")
        status = 500
        started = time.perf_counter()
        HTTP_REQUESTS_ACTIVE.inc(method)

        async def send_with_request_id(message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                response_headers = list(message.get("headers", ()))
                response_headers.append((b"x-request-id", request_id.encode("ascii")))
                message["headers"] = response_headers
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            duration = time.perf_counter() - started
            route = getattr(scope.get("route"), "path", "__unmatched__")
            HTTP_REQUESTS.inc(method, route, str(status))
            HTTP_REQUEST_DURATION.observe(duration, method, route)
            HTTP_REQUESTS_ACTIVE.dec(method)
            log_method = log.error if status >= 500 else log.info
            log_method(
                "http_request_completed",
                method=method,
                route=route,
                status=status,
                duration_ms=round(duration * 1000, 2),
            )
            clear_contextvars()
