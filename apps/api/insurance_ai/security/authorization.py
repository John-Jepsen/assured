"""Tool authorization — enforced independently of the LLM.

Every protected tool passes through ``authorize`` before executing. Two gates:

1. Verification gate: protected tools require a verified session.
2. Ownership gate: any policy/claim/invoice a tool touches must belong to the
   session's bound customer. Cross-customer access is rejected here, in code.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from insurance_ai.db.models import Claim, Invoice, Policy
from insurance_ai.security.session import Session


class AuthorizationError(Exception):
    """Raised when a tool call is not permitted. Carries a structured code."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


@dataclass
class AuthContext:
    session: Session
    db: AsyncSession


async def require_verified(ctx: AuthContext) -> None:
    if not ctx.session.is_verified:
        raise AuthorizationError(
            "not_verified",
            "Identity verification is required before I can access account-specific "
            "information or take this action.",
        )


async def require_owned_policy(ctx: AuthContext, policy_number: str) -> Policy:
    await require_verified(ctx)
    from sqlalchemy import select

    row = await ctx.db.execute(
        select(Policy).where(Policy.policy_number == policy_number.strip().upper())
    )
    policy = row.scalar_one_or_none()
    if policy is None:
        raise AuthorizationError("not_found", f"No policy found for {policy_number}.")
    if policy.customer_id != ctx.session.customer_id:
        # Do not reveal existence of another customer's policy.
        raise AuthorizationError(
            "forbidden", "That policy is not associated with this verified account."
        )
    return policy


async def require_owned_claim(ctx: AuthContext, claim_number: str) -> Claim:
    await require_verified(ctx)
    from sqlalchemy import select

    row = await ctx.db.execute(
        select(Claim).where(Claim.claim_number == claim_number.strip().upper())
    )
    claim = row.scalar_one_or_none()
    if claim is None:
        raise AuthorizationError("not_found", f"No claim found for {claim_number}.")
    if claim.customer_id != ctx.session.customer_id:
        raise AuthorizationError(
            "forbidden", "That claim is not associated with this verified account."
        )
    return claim


async def require_owned_invoice(ctx: AuthContext, invoice_number: str) -> Invoice:
    await require_verified(ctx)
    from sqlalchemy import select

    row = await ctx.db.execute(
        select(Invoice).where(Invoice.invoice_number == invoice_number.strip().upper())
    )
    invoice = row.scalar_one_or_none()
    if invoice is None:
        raise AuthorizationError("not_found", f"No invoice found for {invoice_number}.")
    policy = await ctx.db.get(Policy, invoice.policy_id)
    if policy is None or policy.customer_id != ctx.session.customer_id:
        raise AuthorizationError(
            "forbidden", "That invoice is not associated with this verified account."
        )
    return invoice
