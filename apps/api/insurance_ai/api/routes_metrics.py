"""Authenticated Prometheus metrics endpoint."""

from __future__ import annotations

import secrets

from fastapi import APIRouter, Header, HTTPException, Response, status

from insurance_ai.config import get_settings
from insurance_ai.observability.metrics import REGISTRY

router = APIRouter(tags=["operations"])


@router.get("/metrics", include_in_schema=False)
async def metrics(authorization: str | None = Header(default=None)) -> Response:
    """Expose process metrics only when enabled and separately authenticated."""
    settings = get_settings()
    if not settings.metrics_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    # `secrets.compare_digest` refuses non-ASCII `str` operands with a TypeError, and
    # the ASGI layer hands us headers latin-1 decoded from the wire. Comparing bytes
    # keeps the comparison constant time while making an arbitrary header value a
    # clean 401 instead of an unhandled 500 an anonymous caller can trigger at will.
    # latin-1 reproduces the exact bytes the client sent; "replace" only guards the
    # theoretical case of a value that never came off the wire and can never match.
    expected = f"Bearer {settings.metrics_token.get_secret_value()}".encode()
    if authorization is None or not secrets.compare_digest(
        authorization.encode("latin-1", "replace"), expected
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Metrics authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return Response(content=REGISTRY.render(), media_type="text/plain; version=0.0.4")
