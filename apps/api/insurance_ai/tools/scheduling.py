"""Scheduling tools: agent/adjuster/claims calls, reschedule, cancel."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field
from sqlalchemy import select

from insurance_ai.db.models import Appointment
from insurance_ai.domain.enums import AppointmentStatus, AppointmentType
from insurance_ai.security.authorization import require_verified
from insurance_ai.tools.base import Tool, ToolContext, ToolResult
from insurance_ai.tools.registry import register

SCHED_AGENTS = ("scheduling",)


class ScheduleArgs(BaseModel):
    appointment_type: AppointmentType = Field(..., description="agent_call | adjuster_call | claims_call")
    scheduled_at: datetime = Field(..., description="ISO datetime for the appointment")
    notes: str | None = None


async def _schedule(ctx: ToolContext, args: ScheduleArgs) -> ToolResult:
    await require_verified(ctx.auth)
    appt = Appointment(
        customer_id=ctx.session.customer_id,
        appointment_type=args.appointment_type,
        scheduled_at=args.scheduled_at,
        status=AppointmentStatus.SCHEDULED,
        notes=args.notes,
    )
    ctx.db.add(appt)
    await ctx.db.commit()
    return ToolResult.success(
        {"appointment_id": appt.id, "appointment_type": appt.appointment_type,
         "scheduled_at": appt.scheduled_at.isoformat()},
        message=f"Scheduled a {args.appointment_type.replace('_', ' ')} for {args.scheduled_at:%b %d, %I:%M %p}.",
    )


class RescheduleArgs(BaseModel):
    appointment_id: str
    new_time: datetime


async def _reschedule(ctx: ToolContext, args: RescheduleArgs) -> ToolResult:
    await require_verified(ctx.auth)
    appt = await ctx.db.get(Appointment, args.appointment_id)
    if appt is None or appt.customer_id != ctx.session.customer_id:
        return ToolResult.failure("not_found", "Appointment not found for this account.")
    appt.scheduled_at = args.new_time
    appt.status = AppointmentStatus.RESCHEDULED
    await ctx.db.commit()
    return ToolResult.success(
        {"appointment_id": appt.id, "scheduled_at": appt.scheduled_at.isoformat()},
        message=f"Rescheduled to {args.new_time:%b %d, %I:%M %p}.",
    )


class CancelArgs(BaseModel):
    appointment_id: str


async def _cancel(ctx: ToolContext, args: CancelArgs) -> ToolResult:
    await require_verified(ctx.auth)
    appt = await ctx.db.get(Appointment, args.appointment_id)
    if appt is None or appt.customer_id != ctx.session.customer_id:
        return ToolResult.failure("not_found", "Appointment not found for this account.")
    appt.status = AppointmentStatus.CANCELLED
    await ctx.db.commit()
    return ToolResult.success(
        {"appointment_id": appt.id, "status": appt.status}, message="Appointment cancelled."
    )


async def _list_appointments(ctx: ToolContext, args: BaseModel) -> ToolResult:
    await require_verified(ctx.auth)
    rows = await ctx.db.execute(
        select(Appointment).where(Appointment.customer_id == ctx.session.customer_id)
    )
    appts = rows.scalars().all()
    return ToolResult.success(
        {"appointments": [
            {"appointment_id": a.id, "type": a.appointment_type, "status": a.status,
             "scheduled_at": a.scheduled_at.isoformat()} for a in appts
        ]},
        message=f"You have {len(appts)} appointment(s).",
    )


class _Empty(BaseModel):
    pass


for _tool in (
    Tool("schedule_agent_call", "Schedule a call with an agent.", ScheduleArgs, _schedule,
         requires_verification=True, agents=SCHED_AGENTS),
    Tool("schedule_adjuster_call", "Schedule a call with a claims adjuster.", ScheduleArgs,
         _schedule, requires_verification=True, agents=SCHED_AGENTS),
    Tool("reschedule_appointment", "Reschedule an existing appointment.", RescheduleArgs,
         _reschedule, requires_verification=True, agents=SCHED_AGENTS),
    Tool("cancel_appointment", "Cancel an existing appointment.", CancelArgs, _cancel,
         requires_verification=True, agents=SCHED_AGENTS),
    Tool("list_appointments", "List the customer's appointments.", _Empty, _list_appointments,
         requires_verification=True, agents=SCHED_AGENTS),
):
    register(_tool)
