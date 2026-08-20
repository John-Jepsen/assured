"""Escalation tools: structured human handoff + support tickets."""

from __future__ import annotations

from pydantic import BaseModel, Field

from insurance_ai.db.models import Conversation, SupportTicket
from insurance_ai.domain.enums import Urgency
from insurance_ai.tools._util import next_number
from insurance_ai.tools.base import Tool, ToolContext, ToolResult
from insurance_ai.tools.registry import register

ESC_AGENTS = ("escalation",)


class TransferArgs(BaseModel):
    reason: str = Field(..., description="Why this needs a human")
    urgency: Urgency = Urgency.NORMAL
    summary: str = Field("", description="Short conversation summary for the human")
    intents: list[str] = Field(default_factory=list)
    policy_number: str | None = None
    claim_number: str | None = None


async def _build_handoff(ctx: ToolContext, args: TransferArgs) -> dict:
    conv = await ctx.db.get(Conversation, ctx.conversation_id) if ctx.conversation_id else None
    return {
        "customer_id": ctx.session.customer_id,
        "verification_status": ctx.session.verification_status,
        "detected_intents": args.intents,
        "policy_number": args.policy_number,
        "claim_number": args.claim_number,
        "reason": args.reason,
        "urgency": args.urgency,
        "summary": args.summary or (conv.summary if conv else ""),
        "channel": conv.channel if conv else "web",
    }


async def _transfer_to_human(ctx: ToolContext, args: TransferArgs) -> ToolResult:
    handoff = await _build_handoff(ctx, args)
    ticket = SupportTicket(
        ticket_number=await next_number(ctx.db, "SUPPORT"),
        customer_id=ctx.session.customer_id,
        conversation_id=ctx.conversation_id,
        status="open",
        urgency=args.urgency,
        reason=args.reason,
        summary=args.summary or f"Escalation: {args.reason}",
        handoff=handoff,
    )
    ctx.db.add(ticket)
    if ctx.conversation_id:
        conv = await ctx.db.get(Conversation, ctx.conversation_id)
        if conv:
            conv.escalated = True
    ctx.session.escalated = True
    await ctx.db.commit()
    return ToolResult.success(
        {"ticket_number": ticket.ticket_number, "urgency": args.urgency, "handoff": handoff},
        message=(
            f"I've escalated this to our support team. Your ticket is {ticket.ticket_number}. "
            "A licensed representative will follow up."
        ),
    )


class TicketArgs(BaseModel):
    reason: str
    summary: str
    urgency: Urgency = Urgency.NORMAL


async def _create_ticket(ctx: ToolContext, args: TicketArgs) -> ToolResult:
    ticket = SupportTicket(
        ticket_number=await next_number(ctx.db, "SUPPORT"),
        customer_id=ctx.session.customer_id,
        conversation_id=ctx.conversation_id,
        status="open",
        urgency=args.urgency,
        reason=args.reason,
        summary=args.summary,
        handoff={"customer_id": ctx.session.customer_id},
    )
    ctx.db.add(ticket)
    await ctx.db.commit()
    return ToolResult.success(
        {"ticket_number": ticket.ticket_number},
        message=f"Support ticket {ticket.ticket_number} created.",
    )


for _tool in (
    Tool("transfer_to_human", "Escalate to a human with a structured handoff.", TransferArgs,
         _transfer_to_human, requires_verification=False, agents=ESC_AGENTS),
    Tool("create_support_ticket", "Create a support ticket.", TicketArgs, _create_ticket,
         requires_verification=False, agents=ESC_AGENTS),
):
    register(_tool)
