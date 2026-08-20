"""Payment provider abstraction: mock (default) + Stripe test-mode adapter."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from insurance_ai.config import Settings, get_settings


@dataclass
class PaymentIntentResult:
    ok: bool
    reference: str
    status: str  # paid | failed | requires_action
    method: str
    message: str = ""
    test_mode: bool = True


class PaymentProvider:
    name = "base"
    test_mode = True

    async def charge(self, amount: float, currency: str, description: str) -> PaymentIntentResult:
        raise NotImplementedError


class MockPaymentProvider(PaymentProvider):
    """Deterministic mock. Amount ending in .99 simulates a decline (for tests)."""

    name = "mock"
    test_mode = True

    async def charge(self, amount: float, currency: str, description: str) -> PaymentIntentResult:
        cents = round(amount * 100) % 100
        if cents == 99:
            return PaymentIntentResult(
                ok=False, reference="mock_decline", status="failed", method="mock",
                message="Payment was declined (mock simulated decline).",
            )
        ref = f"mock_{abs(hash((round(amount, 2), description))) % 10_000_000:07d}"
        return PaymentIntentResult(
            ok=True, reference=ref, status="paid", method="mock",
            message=f"Mock payment of {amount:.2f} {currency.upper()} succeeded (TEST MODE).",
        )


class StripeTestPaymentProvider(PaymentProvider):
    """Stripe test-mode PaymentIntent. Requires the `stripe` extra + test key."""

    name = "stripe"
    test_mode = True

    def __init__(self, settings: Settings) -> None:
        import stripe  # imported lazily; only when selected

        self._stripe = stripe
        stripe.api_key = settings.stripe_secret_key

    async def charge(self, amount: float, currency: str, description: str) -> PaymentIntentResult:
        import asyncio

        def _create():
            return self._stripe.PaymentIntent.create(
                amount=int(round(amount * 100)),
                currency=currency,
                description=description,
                payment_method="pm_card_visa",  # Stripe test token
                confirm=True,
                automatic_payment_methods={"enabled": True, "allow_redirects": "never"},
            )

        try:
            intent = await asyncio.to_thread(_create)
        except Exception as e:  # surface as a structured failure, never a stack trace
            return PaymentIntentResult(
                ok=False, reference="stripe_error", status="failed", method="stripe",
                message=f"Stripe test charge failed: {type(e).__name__}.",
            )
        paid = intent.status == "succeeded"
        return PaymentIntentResult(
            ok=paid, reference=intent.id, status="paid" if paid else intent.status,
            method="stripe",
            message=("Stripe test payment succeeded." if paid else f"Status: {intent.status}."),
        )


@lru_cache
def get_payment_provider() -> PaymentProvider:
    settings = get_settings()
    if settings.is_stripe_enabled:
        return StripeTestPaymentProvider(settings)
    return MockPaymentProvider()
