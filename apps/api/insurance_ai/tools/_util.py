"""Shared tool helpers."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from insurance_ai.db.models import Claim, SupportTicket

_PREFIX_BASE = {"SUPPORT": 50000, "CLAIM": 90000}


async def next_number(db: AsyncSession, prefix: str) -> str:
    """Generate a readable sequential id like SUPPORT-50001.

    Uses a count against the owning table so numbers are stable and collision-free
    within a seeded database.
    """
    base = _PREFIX_BASE.get(prefix, 10000)
    if prefix == "SUPPORT":
        count = await db.scalar(select(func.count()).select_from(SupportTicket))
    elif prefix == "CLAIM":
        count = await db.scalar(select(func.count()).select_from(Claim))
    else:
        count = 0
    return f"{prefix}-{base + int(count or 0) + 1}"
