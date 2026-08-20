"""Payments: config/status + Stripe webhook (gated). Never live payments."""

from __future__ import annotations

from fastapi import APIRouter, Request

from insurance_ai.config import get_settings
from insurance_ai.observability import get_logger
from insurance_ai.payments import get_payment_provider

router = APIRouter(prefix="/api/payments", tags=["payments"])
log = get_logger("payments")


@router.get("/config")
async def payment_config() -> dict:
    settings = get_settings()
    provider = get_payment_provider()
    return {
        "provider": provider.name,
        "test_mode": True,  # always test mode; live payments are never enabled
        "stripe_configured": settings.is_stripe_enabled,
        "note": "Mock provider active." if provider.name == "mock" else "Stripe TEST mode active.",
    }


@router.post("/webhook")
async def stripe_webhook(request: Request) -> dict:
    """Stripe webhook receiver (test mode). Verifies signature if secret is set."""
    settings = get_settings()
    if not settings.is_stripe_enabled:
        return {"received": False, "reason": "stripe_not_configured"}
    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")
    if settings.stripe_webhook_secret:
        try:
            import stripe

            event = stripe.Webhook.construct_event(payload, sig, settings.stripe_webhook_secret)
            log.info("stripe_webhook", event_type=event.get("type"))
            return {"received": True, "type": event.get("type")}
        except Exception as e:
            log.error("stripe_webhook_invalid", error=type(e).__name__)
            return {"received": False, "reason": "invalid_signature"}
    return {"received": True, "verified": False}
