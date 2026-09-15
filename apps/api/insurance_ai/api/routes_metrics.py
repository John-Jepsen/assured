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

    expected = f"Bearer {settings.metrics_token.get_secret_value()}"
    if authorization is None or not secrets.compare_digest(authorization, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Metrics authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return Response(content=REGISTRY.render(), media_type="text/plain; version=0.0.4")
