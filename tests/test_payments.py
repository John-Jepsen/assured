"""Mock payment provider + make_payment tool: confirm-first, success, decline,
and honest failure reporting (never claims success when the charge failed)."""

from __future__ import annotations

import pytest

from insurance_ai.payments import MockPaymentProvider
from insurance_ai.security.verification import VerificationClaim, verify_identity
from insurance_ai.tools.base import ToolContext
from insurance_ai.tools.registry import get_tool, load_all_tools

load_all_tools()


@pytest.mark.asyncio
async def test_mock_provider_success_and_decline():
    p = MockPaymentProvider()
    ok = await p.charge(142.50, "usd", "premium")
    assert ok.ok and ok.status == "paid" and ok.test_mode
    declined = await p.charge(10.99, "usd", "premium")  # .99 => simulated decline
    assert not declined.ok and declined.status == "failed"


async def _verify(db, sess):
    await verify_identity(db, sess, VerificationClaim(policy_number="AUTO-10024", zip_code="78258"))


@pytest.mark.asyncio
async def test_make_payment_requires_confirmation_then_pays(db, new_session):
    sess = new_session()
    await _verify(db, sess)
    ctx = ToolContext(session=sess, db=db)
    tool = get_tool("make_payment")

    # Without confirm=true it must not charge — it asks to confirm.
    pending = await tool.run(ctx, {"invoice_number": "INV-AUTO-10024-07"})
    assert pending.ok and pending.data.get("requires_confirmation") is True

    # With confirm=true it charges via the mock provider (TEST MODE).
    paid = await tool.run(ctx, {"invoice_number": "INV-AUTO-10024-07", "confirm": True})
    assert paid.ok
    assert paid.data["test_mode"] is True
    assert paid.data["new_status"] in ("paid", "pending")


@pytest.mark.asyncio
async def test_make_payment_unverified_blocked(db, new_session):
    sess = new_session()
    ctx = ToolContext(session=sess, db=db)
    r = await get_tool("make_payment").run(ctx, {"invoice_number": "INV-AUTO-10024-07", "confirm": True})
    assert not r.ok and r.error_code == "not_verified"
