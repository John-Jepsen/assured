"""Health + readiness endpoints (used by Docker healthchecks)."""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import text

from insurance_ai import __version__
from insurance_ai.api.schemas import HealthStatus
from insurance_ai.config import get_settings
from insurance_ai.db.base import get_engine

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthStatus)
async def health() -> HealthStatus:
    settings = get_settings()
    db_ok = "unknown"
    try:
        async with get_engine().connect() as conn:
            await conn.execute(text("SELECT 1"))
        db_ok = "ok"
    except Exception:
        db_ok = "unavailable"
    return HealthStatus(
        status="ok" if db_ok == "ok" else "degraded",
        version=__version__,
        providers={
            "llm": settings.llm_provider, "stt": settings.stt_provider,
            "tts": settings.tts_provider, "embedding": settings.embedding_provider,
            "vector_backend": settings.vector_backend,
        },
        database=db_ok,
        features={
            "stripe": settings.is_stripe_enabled,
            "telephony": settings.is_telephony_enabled,
            "payment_provider": settings.payment_provider,
        },
    )


@router.get("/ready")
async def ready() -> dict:
    """Readiness: DB reachable. Returns 503-worthy body the compose healthcheck reads."""
    try:
        async with get_engine().connect() as conn:
            await conn.execute(text("SELECT 1"))
        return {"ready": True}
    except Exception:
        return {"ready": False}
