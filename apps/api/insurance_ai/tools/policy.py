"""Policy tools: lookup, coverages, changes, contact updates."""

from __future__ import annotations

from pydantic import BaseModel, Field
from sqlalchemy import select

from insurance_ai.db.models import Coverage, InsuredAsset, Policy
from insurance_ai.security.authorization import require_owned_policy, require_verified
from insurance_ai.tools.base import Source, Tool, ToolContext, ToolResult
from insurance_ai.tools.registry import register

POLICY_AGENTS = ("policy", "account")


def _policy_source(policy: Policy) -> Source:
    return Source(citation=f"{policy.product_type.title()} Policy {policy.policy_number}")


class PolicyNumberArgs(BaseModel):
    policy_number: str = Field(..., description="Policy identifier, e.g. AUTO-10024")


async def _lookup_policy(ctx: ToolContext, args: PolicyNumberArgs) -> ToolResult:
    policy = await require_owned_policy(ctx.auth, args.policy_number)
    return ToolResult.success(
        {
            "policy_number": policy.policy_number,
            "product_type": policy.product_type,
            "status": policy.status,
            "effective_date": str(policy.effective_date),
            "renewal_date": str(policy.renewal_date),
            "premium_amount": float(policy.premium_amount),
            "billing_cadence": policy.billing_cadence,
            "autopay": policy.autopay,
            "details": policy.details,
        },
        message=f"Policy {policy.policy_number} is {policy.status}.",
        sources=[_policy_source(policy)],
    )


async def _lookup_coverages(ctx: ToolContext, args: PolicyNumberArgs) -> ToolResult:
    policy = await require_owned_policy(ctx.auth, args.policy_number)
    rows = await ctx.db.execute(select(Coverage).where(Coverage.policy_id == policy.id))
    coverages = rows.scalars().all()
    return ToolResult.success(
        {
            "policy_number": policy.policy_number,
            "coverages": [
                {
                    "coverage_type": c.coverage_type,
                    "limit_amount": float(c.limit_amount) if c.limit_amount is not None else None,
                    "deductible": float(c.deductible) if c.deductible is not None else None,
                    "per_unit": c.per_unit,
                    "exclusions": c.exclusions,
                    "description": c.description,
                }
                for c in coverages
            ],
        },
        message=f"Found {len(coverages)} coverage(s) on {policy.policy_number}.",
        sources=[
            _policy_source(policy),
            Source(citation=f"Coverage Schedule — {policy.policy_number}"),
        ],
    )


async def _lookup_assets(ctx: ToolContext, args: PolicyNumberArgs) -> ToolResult:
    policy = await require_owned_policy(ctx.auth, args.policy_number)
    rows = await ctx.db.execute(select(InsuredAsset).where(InsuredAsset.policy_id == policy.id))
    assets = rows.scalars().all()
    return ToolResult.success(
        {
            "policy_number": policy.policy_number,
            "assets": [
                {
                    "asset_type": a.asset_type,
                    "description": a.description,
                    "identifier": a.identifier,
                    "attributes": a.attributes,
                }
                for a in assets
            ],
        },
        message=f"{len(assets)} insured asset(s) on {policy.policy_number}.",
        sources=[_policy_source(policy)],
    )


class ContactUpdateArgs(BaseModel):
    email: str | None = None
    phone: str | None = None
    address: str | None = None
    comm_preference: str | None = Field(None, description="email | sms | phone | mail")


async def _update_contact(ctx: ToolContext, args: ContactUpdateArgs) -> ToolResult:
    await require_verified(ctx.auth)
    from insurance_ai.db.models import Customer

    customer = await ctx.db.get(Customer, ctx.session.customer_id)
    if customer is None:
        return ToolResult.failure("not_found", "Customer record not found.")
    updated = {}
    for field_name in ("email", "phone", "address", "comm_preference"):
        val = getattr(args, field_name)
        if val:
            setattr(customer, field_name, val)
            updated[field_name] = val
    if not updated:
        return ToolResult.failure("invalid_arguments", "No contact fields provided to update.")
    await ctx.db.commit()
    return ToolResult.success({"updated": updated}, message="Contact information updated.")


class PolicyChangeArgs(BaseModel):
    policy_number: str
    change_type: str = Field(..., description="e.g. add_vehicle, remove_vehicle, update_coverage")
    details: str = Field(..., description="Human description of the requested change")


async def _request_policy_change(ctx: ToolContext, args: PolicyChangeArgs) -> ToolResult:
    policy = await require_owned_policy(ctx.auth, args.policy_number)
    # Simulated workflow: record a follow-up request rather than mutating coverage silently.
    from insurance_ai.db.models import SupportTicket
    from insurance_ai.tools._util import next_number

    ticket = SupportTicket(
        ticket_number=await next_number(ctx.db, "SUPPORT"),
        customer_id=ctx.session.customer_id,
        conversation_id=ctx.conversation_id,
        status="open",
        urgency="normal",
        reason=f"policy_change:{args.change_type}",
        summary=f"Requested change to {policy.policy_number}: {args.details}",
        handoff={"policy_number": policy.policy_number, "change_type": args.change_type},
    )
    ctx.db.add(ticket)
    await ctx.db.commit()
    return ToolResult.success(
        {"ticket_number": ticket.ticket_number, "change_type": args.change_type},
        message=(
            f"I've logged a policy-change request ({ticket.ticket_number}). A licensed "
            "representative will confirm the change; it is not applied automatically."
        ),
        sources=[_policy_source(policy)],
    )


register(
    Tool(
        "lookup_policy",
        "Retrieve a policy's status, dates, premium, and structured details.",
        PolicyNumberArgs,
        _lookup_policy,
        requires_verification=True,
        agents=POLICY_AGENTS,
    )
)
register(
    Tool(
        "lookup_coverages",
        "Retrieve coverages, limits, deductibles, and exclusions for a policy.",
        PolicyNumberArgs,
        _lookup_coverages,
        requires_verification=True,
        agents=POLICY_AGENTS,
    )
)
register(
    Tool(
        "lookup_insured_assets",
        "List insured assets (vehicles, dwellings, insured members) on a policy.",
        PolicyNumberArgs,
        _lookup_assets,
        requires_verification=True,
        agents=POLICY_AGENTS,
    )
)
register(
    Tool(
        "update_contact_information",
        "Update the verified customer's contact details / communication preference.",
        ContactUpdateArgs,
        _update_contact,
        requires_verification=True,
        agents=("account", "policy"),
    )
)
register(
    Tool(
        "request_policy_change",
        "Log a simulated policy-change request for representative confirmation.",
        PolicyChangeArgs,
        _request_policy_change,
        requires_verification=True,
        agents=POLICY_AGENTS,
    )
)
