"""Exact browser-origin enforcement shared by browser WebSocket endpoints."""

from __future__ import annotations

from insurance_ai.config import Settings, get_settings


def websocket_origin_allowed(origin: str | None, settings: Settings | None = None) -> bool:
    """Allow exact configured origins; origin-less clients are development-only."""
    settings = settings or get_settings()
    if origin is None:
        return settings.environment != "prod"
    return origin in settings.allowed_origins
