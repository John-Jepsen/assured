"""Claims tools: lookup, status, FNOL creation, info, adjuster, disputes."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field

from insurance_ai.db.models import Claim
from insurance_ai.domain.enums import ClaimStatus
from insurance_ai.security.authorization import require_owned_claim, require_owned_policy
from insurance_ai.tools._util import next_number
from insurance_ai.tools.base import Source, Tool, ToolContext, ToolResult
from insurance_ai.tools.registry import register

CLAIMS_AGENTS = ("claims",)


def _claim_dict(claim: Claim) -> dict:
    return {
        "claim_number": claim.claim_number,
        "status": claim.status,
        "loss_type": claim.loss_type,
        "description": claim.description,
        "date_of_loss": str(claim.date_of_loss),
        "reported_date": str(claim.reported_date),
        "adjuster_name": claim.adjuster_name,
        "adjuster_phone": claim.adjuster_phone,
        "next_steps": claim.next_steps,
        "requested_documents": claim.requested_documents,
    }


class ClaimNumberArgs(BaseModel):
    claim_number: str = Field(..., description="Claim identifier, e.g. CLAIM-90001")


async def _lookup_claim(ctx: ToolContext, args: ClaimNumberArgs) -> ToolResult:
    claim = await require_owned_claim(ctx.auth, args.claim_number)
    return ToolResult.success(
        _claim_dict(claim),
        message=f"Claim {claim.claim_number} is {claim.status}.",
        sources=[Source(citation=f"Claim File {claim.claim_number}")],
    )


async def _get_claim_status(ctx: ToolContext, args: ClaimNumberArgs) -> ToolResult:
    claim = await require_owned_claim(ctx.auth, args.claim_number)
    return ToolResult.success(
        {
            "claim_number": claim.claim_number,
            "status": claim.status,
            "next_steps": claim.next_steps,
            "requested_documents": claim.requested_documents,
        },
        message=f"Claim {claim.claim_number} status: {claim.status}.",
        sources=[Source(citation=f"Claim File {claim.claim_number}")],
    )


async def _get_adjuster(ctx: ToolContext, args: ClaimNumberArgs) -> ToolResult:
    claim = await require_owned_claim(ctx.auth, args.claim_number)
    if not claim.adjuster_name:
        return ToolResult.success(
            {"claim_number": claim.claim_number, "adjuster_name": None},
            message="No adjuster has been assigned to this claim yet.",
            sources=[Source(citation=f"Claim File {claim.claim_number}")],
        )
    return ToolResult.success(
        {
            "claim_number": claim.claim_number,
            "adjuster_name": claim.adjuster_name,
            "adjuster_phone": claim.adjuster_phone,
        },
        message=f"Adjuster {claim.adjuster_name} is assigned to {claim.claim_number}.",
        sources=[Source(citation=f"Claim File {claim.claim_number}")],
    )


class CreateClaimArgs(BaseModel):
    policy_number: str
    loss_type: str = Field(..., description="e.g. collision, water_damage, theft")
    description: str
    date_of_loss: date


async def _create_claim(ctx: ToolContext, args: CreateClaimArgs) -> ToolResult:
    policy = await require_owned_policy(ctx.auth, args.policy_number)
    claim = Claim(
        claim_number=await next_number(ctx.db, "CLAIM"),
        policy_id=policy.id,
        customer_id=ctx.session.customer_id,
        status=ClaimStatus.FNOL,
        loss_type=args.loss_type,
        description=args.description,
        date_of_loss=args.date_of_loss,
        next_steps=["An adjuster will be assigned within 1 business day."],
        requested_documents=["Photos of the loss", "Any police/incident report"],
    )
    ctx.db.add(claim)
    await ctx.db.commit()
    return ToolResult.success(
        _claim_dict(claim),
        message=(
            f"I've filed a first notice of loss. Your claim number is {claim.claim_number}. "
            "This does not guarantee coverage; an adjuster will review it."
        ),
        sources=[Source(citation=f"Claim File {claim.claim_number}")],
    )


class AddClaimInfoArgs(BaseModel):
    claim_number: str
    note: str = Field(..., description="Additional information to attach to the claim")


async def _add_claim_information(ctx: ToolContext, args: AddClaimInfoArgs) -> ToolResult:
    claim = await require_owned_claim(ctx.auth, args.claim_number)
    notes = list(claim.notes or [])
    notes.append(args.note)
    claim.notes = notes
    await ctx.db.commit()
    return ToolResult.success(
        {"claim_number": claim.claim_number, "notes_count": len(notes)},
        message="I've added that information to your claim.",
        sources=[Source(citation=f"Claim File {claim.claim_number}")],
    )


async def _dispute_claim(ctx: ToolContext, args: ClaimNumberArgs) -> ToolResult:
    claim = await require_owned_claim(ctx.auth, args.claim_number)
    from insurance_ai.db.models import SupportTicket

    ticket = SupportTicket(
        ticket_number=await next_number(ctx.db, "SUPPORT"),
        customer_id=ctx.session.customer_id,
        conversation_id=ctx.conversation_id,
        status="open",
        urgency="high",
        reason="claim_dispute",
        summary=f"Customer disputes claim {claim.claim_number} (status {claim.status}).",
        handoff={"claim_number": claim.claim_number},
    )
    claim.status = ClaimStatus.DISPUTED
    ctx.db.add(ticket)
    await ctx.db.commit()
    return ToolResult.success(
        {"claim_number": claim.claim_number, "ticket_number": ticket.ticket_number},
        message=(
            f"I've opened a dispute review ({ticket.ticket_number}) and flagged claim "
            f"{claim.claim_number}. A licensed claims representative will follow up."
        ),
        sources=[Source(citation=f"Claim File {claim.claim_number}")],
    )


for _tool in (
    Tool(
        "lookup_claim",
        "Retrieve full details of a claim.",
        ClaimNumberArgs,
        _lookup_claim,
        requires_verification=True,
        agents=CLAIMS_AGENTS,
    ),
    Tool(
        "get_claim_status",
        "Get a claim's status and next steps.",
        ClaimNumberArgs,
        _get_claim_status,
        requires_verification=True,
        agents=CLAIMS_AGENTS,
    ),
    Tool(
        "get_adjuster_info",
        "Retrieve the adjuster assigned to a claim.",
        ClaimNumberArgs,
        _get_adjuster,
        requires_verification=True,
        agents=CLAIMS_AGENTS,
    ),
    Tool(
        "create_claim",
        "File a simulated first notice of loss.",
        CreateClaimArgs,
        _create_claim,
        requires_verification=True,
        agents=CLAIMS_AGENTS,
    ),
    Tool(
        "add_claim_information",
        "Attach additional information to a claim.",
        AddClaimInfoArgs,
        _add_claim_information,
        requires_verification=True,
        agents=CLAIMS_AGENTS,
    ),
    Tool(
        "escalate_claim_dispute",
        "Open a dispute review on a claim.",
        ClaimNumberArgs,
        _dispute_claim,
        requires_verification=True,
        agents=CLAIMS_AGENTS,
    ),
):
    register(_tool)
