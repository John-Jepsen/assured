"""Billing tools: status, history, next payment, make payment."""

from __future__ import annotations

from pydantic import BaseModel, Field
from sqlalchemy import select

from insurance_ai.db.models import Invoice, Payment, Policy
from insurance_ai.domain.enums import PaymentStatus
from insurance_ai.payments import get_payment_provider
from insurance_ai.security.authorization import require_owned_invoice, require_owned_policy
from insurance_ai.tools.base import Source, Tool, ToolContext, ToolResult
from insurance_ai.tools.registry import register

BILLING_AGENTS = ("billing",)


class PolicyNumberArgs(BaseModel):
    policy_number: str = Field(..., description="Policy identifier, e.g. AUTO-10024")


async def _invoices_for(ctx: ToolContext, policy: Policy) -> list[Invoice]:
    rows = await ctx.db.execute(
        select(Invoice).where(Invoice.policy_id == policy.id).order_by(Invoice.due_date)
    )
    return list(rows.scalars().all())


async def _get_billing_status(ctx: ToolContext, args: PolicyNumberArgs) -> ToolResult:
    policy = await require_owned_policy(ctx.auth, args.policy_number)
    invoices = await _invoices_for(ctx, policy)
    outstanding = [i for i in invoices if i.status in (PaymentStatus.PENDING, PaymentStatus.PAST_DUE, PaymentStatus.SCHEDULED)]
    balance = sum(float(i.amount_due) - float(i.amount_paid) for i in outstanding)
    next_due = min((i.due_date for i in outstanding), default=None)
    return ToolResult.success(
        {
            "policy_number": policy.policy_number,
            "premium_amount": float(policy.premium_amount),
            "billing_cadence": policy.billing_cadence,
            "autopay": policy.autopay,
            "current_balance": round(balance, 2),
            "next_payment_date": str(next_due) if next_due else None,
            "past_due": any(i.status == PaymentStatus.PAST_DUE for i in invoices),
        },
        message=(
            f"Balance on {policy.policy_number} is ${balance:.2f}."
            + (f" Next payment due {next_due}." if next_due else "")
        ),
        sources=[Source(citation=f"Billing Account — {policy.policy_number}")],
    )


async def _get_payment_history(ctx: ToolContext, args: PolicyNumberArgs) -> ToolResult:
    policy = await require_owned_policy(ctx.auth, args.policy_number)
    invoices = await _invoices_for(ctx, policy)
    history = []
    for inv in invoices:
        rows = await ctx.db.execute(select(Payment).where(Payment.invoice_id == inv.id))
        for p in rows.scalars().all():
            history.append(
                {
                    "invoice_number": inv.invoice_number,
                    "amount": float(p.amount),
                    "status": p.status,
                    "method": p.method,
                    "created_at": p.created_at.isoformat(),
                }
            )
    return ToolResult.success(
        {"policy_number": policy.policy_number, "payments": history},
        message=f"{len(history)} payment record(s) for {policy.policy_number}.",
        sources=[Source(citation=f"Billing Account — {policy.policy_number}")],
    )


class MakePaymentArgs(BaseModel):
    invoice_number: str = Field(..., description="Invoice to pay, e.g. INV-10024-03")
    amount: float | None = Field(None, description="Amount; defaults to full balance due")
    confirm: bool = Field(False, description="Must be true to actually charge")


async def _make_payment(ctx: ToolContext, args: MakePaymentArgs) -> ToolResult:
    invoice = await require_owned_invoice(ctx.auth, args.invoice_number)
    balance = float(invoice.amount_due) - float(invoice.amount_paid)
    amount = args.amount if args.amount is not None else balance
    if amount <= 0:
        return ToolResult.failure("nothing_due", "This invoice has no outstanding balance.")
    if amount > balance + 0.001:
        return ToolResult.failure(
            "amount_too_high", f"Amount ${amount:.2f} exceeds the balance of ${balance:.2f}."
        )
    if not args.confirm:
        # Sensitive action — require explicit confirmation before charging.
        return ToolResult.success(
            {"requires_confirmation": True, "invoice_number": invoice.invoice_number,
             "amount": round(amount, 2), "balance": round(balance, 2)},
            message=(
                f"You're about to pay ${amount:.2f} toward {invoice.invoice_number}. "
                "Shall I confirm this payment?"
            ),
        )

    provider = get_payment_provider()
    result = await provider.charge(amount, "usd", f"Premium payment {invoice.invoice_number}")
    payment = Payment(
        invoice_id=invoice.id, amount=amount,
        status=PaymentStatus.PAID if result.ok else PaymentStatus.FAILED,
        method=result.method, provider_reference=result.reference,
    )
    ctx.db.add(payment)
    if result.ok:
        invoice.amount_paid = float(invoice.amount_paid) + amount
        invoice.status = (
            PaymentStatus.PAID
            if invoice.amount_paid >= float(invoice.amount_due) - 0.001
            else PaymentStatus.PENDING
        )
    await ctx.db.commit()
    if not result.ok:
        # Never claim success when the charge failed.
        return ToolResult.failure("payment_failed", result.message)
    return ToolResult.success(
        {"invoice_number": invoice.invoice_number, "amount_paid": round(amount, 2),
         "reference": result.reference, "test_mode": provider.test_mode,
         "new_status": invoice.status},
        message=result.message,
        sources=[Source(citation=f"Billing Account — {invoice.invoice_number}")],
    )


for _tool in (
    Tool("get_billing_status", "Retrieve balance, next payment date, autopay status.",
         PolicyNumberArgs, _get_billing_status, requires_verification=True, agents=BILLING_AGENTS),
    Tool("get_payment_history", "Retrieve payment history for a policy.", PolicyNumberArgs,
         _get_payment_history, requires_verification=True, agents=BILLING_AGENTS),
    Tool("make_payment", "Make a premium payment (TEST MODE). Requires confirm=true to charge.",
         MakePaymentArgs, _make_payment, requires_verification=True, agents=BILLING_AGENTS),
):
    register(_tool)
