"""Admin dashboard API: conversations, traces, tickets, tool activity, evals."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from insurance_ai.api.service import ConversationService
from insurance_ai.db.base import get_session
from insurance_ai.db.models import (
    Conversation,
    Customer,
    EvaluationRun,
    Policy,
    SupportTicket,
    ToolExecution,
)
from insurance_ai.db.seed import DEMO_CUSTOMERS

router = APIRouter(prefix="/api/admin", tags=["admin"])
_service = ConversationService()


@router.get("/demo-customers")
async def demo_customers() -> dict:
    return {"synthetic": True, "customers": DEMO_CUSTOMERS}


@router.get("/conversations")
async def conversations(db: AsyncSession = Depends(get_session), limit: int = 50) -> dict:
    rows = await db.execute(
        select(Conversation).order_by(desc(Conversation.updated_at)).limit(limit)
    )
    return {
        "conversations": [
            {
                "id": c.id,
                "channel": c.channel,
                "verification_status": c.verification_status,
                "current_agent": c.current_agent,
                "escalated": c.escalated,
                "customer_id": c.customer_id,
                "updated_at": c.updated_at.isoformat(),
            }
            for c in rows.scalars().all()
        ]
    }


@router.get("/conversations/{conversation_id}")
async def conversation_detail(
    conversation_id: str, db: AsyncSession = Depends(get_session)
) -> dict:
    conv = await db.get(Conversation, conversation_id)
    if not conv:
        return {"error": "not_found"}
    transcript = await _service.transcript(db, conversation_id)
    tools = await db.execute(
        select(ToolExecution)
        .where(ToolExecution.conversation_id == conversation_id)
        .order_by(ToolExecution.created_at)
    )
    return {
        "conversation": {
            "id": conv.id,
            "channel": conv.channel,
            "verification_status": conv.verification_status,
            "current_agent": conv.current_agent,
            "escalated": conv.escalated,
        },
        "transcript": transcript,
        "tool_executions": [
            {
                "tool_name": t.tool_name,
                "ok": t.ok,
                "error_code": t.error_code,
                "arguments": t.arguments,
                "latency_ms": round(t.latency_ms, 1),
                "result_message": t.result.get("message", ""),
            }
            for t in tools.scalars().all()
        ],
    }


@router.get("/customers/{customer_id}")
async def customer_profile(customer_id: str, db: AsyncSession = Depends(get_session)) -> dict:
    c = await db.get(Customer, customer_id)
    if not c:
        return {"error": "not_found"}
    policies = await db.execute(select(Policy).where(Policy.customer_id == customer_id))
    return {
        "name": f"{c.first_name} {c.last_name}",
        "email": c.email[:2] + "***@" + c.email.split("@")[1],
        "zip_code": c.zip_code,
        "synthetic": c.is_synthetic,
        "policies": [
            {"policy_number": p.policy_number, "product_type": p.product_type, "status": p.status}
            for p in policies.scalars().all()
        ],
    }


@router.get("/tickets")
async def tickets(db: AsyncSession = Depends(get_session)) -> dict:
    rows = await db.execute(select(SupportTicket).order_by(desc(SupportTicket.created_at)))
    return {
        "tickets": [
            {
                "ticket_number": t.ticket_number,
                "status": t.status,
                "urgency": t.urgency,
                "reason": t.reason,
                "summary": t.summary,
                "handoff": t.handoff,
                "created_at": t.created_at.isoformat(),
            }
            for t in rows.scalars().all()
        ]
    }


@router.get("/tools")
async def tool_activity(db: AsyncSession = Depends(get_session), limit: int = 100) -> dict:
    rows = await db.execute(
        select(ToolExecution).order_by(desc(ToolExecution.created_at)).limit(limit)
    )
    items = list(rows.scalars().all())
    return {
        "tool_executions": [
            {
                "tool_name": t.tool_name,
                "ok": t.ok,
                "error_code": t.error_code,
                "latency_ms": round(t.latency_ms, 1),
                "conversation_id": t.conversation_id,
                "created_at": t.created_at.isoformat(),
            }
            for t in items
        ],
        "avg_latency_ms": round(sum(t.latency_ms for t in items) / len(items), 1) if items else 0,
    }


@router.get("/evaluations")
async def evaluations(db: AsyncSession = Depends(get_session)) -> dict:
    rows = await db.execute(
        select(EvaluationRun).order_by(desc(EvaluationRun.created_at)).limit(20)
    )
    return {
        "runs": [
            {
                "suite": r.suite,
                "total": r.total,
                "passed": r.passed,
                "pass_rate": round(r.passed / r.total, 3) if r.total else 0,
                "created_at": r.created_at.isoformat(),
            }
            for r in rows.scalars().all()
        ]
    }
