"""Customer account tools: profile, associated policies/claims, preferences."""

from __future__ import annotations

from pydantic import BaseModel
from sqlalchemy import select

from insurance_ai.db.models import Claim, Customer, Policy
from insurance_ai.security.authorization import require_verified
from insurance_ai.tools.base import Tool, ToolContext, ToolResult
from insurance_ai.tools.registry import register

ACCOUNT_AGENTS = ("account",)


class NoArgs(BaseModel):
    pass


def _mask_email(email: str) -> str:
    name, _, domain = email.partition("@")
    return (name[:2] + "***@" + domain) if name else email


async def _get_customer(ctx: ToolContext, args: NoArgs) -> ToolResult:
    await require_verified(ctx.auth)
    customer = await ctx.db.get(Customer, ctx.session.customer_id)
    if customer is None:
        return ToolResult.failure("not_found", "Customer record not found.")
    return ToolResult.success(
        {
            "name": f"{customer.first_name} {customer.last_name}",
            "email": _mask_email(customer.email),
            "phone": "***-***-" + customer.phone[-4:],
            "zip_code": customer.zip_code,
            "comm_preference": customer.comm_preference,
        },
        message="Here is your account profile.",
    )


async def _list_policies(ctx: ToolContext, args: NoArgs) -> ToolResult:
    await require_verified(ctx.auth)
    rows = await ctx.db.execute(select(Policy).where(Policy.customer_id == ctx.session.customer_id))
    policies = rows.scalars().all()
    return ToolResult.success(
        {
            "policies": [
                {
                    "policy_number": p.policy_number,
                    "product_type": p.product_type,
                    "status": p.status,
                }
                for p in policies
            ]
        },
        message=f"You have {len(policies)} policy(ies) on file.",
    )


async def _list_claims(ctx: ToolContext, args: NoArgs) -> ToolResult:
    await require_verified(ctx.auth)
    rows = await ctx.db.execute(select(Claim).where(Claim.customer_id == ctx.session.customer_id))
    claims = rows.scalars().all()
    return ToolResult.success(
        {
            "claims": [
                {"claim_number": c.claim_number, "status": c.status, "loss_type": c.loss_type}
                for c in claims
            ]
        },
        message=f"You have {len(claims)} claim(s) on file.",
    )


for _tool in (
    Tool(
        "lookup_customer",
        "Retrieve the verified customer's profile (masked).",
        NoArgs,
        _get_customer,
        requires_verification=True,
        agents=ACCOUNT_AGENTS,
    ),
    Tool(
        "list_policies",
        "List the verified customer's policies.",
        NoArgs,
        _list_policies,
        requires_verification=True,
        agents=ACCOUNT_AGENTS,
    ),
    Tool(
        "list_claims",
        "List the verified customer's claims.",
        NoArgs,
        _list_claims,
        requires_verification=True,
        agents=ACCOUNT_AGENTS,
    ),
):
    register(_tool)
