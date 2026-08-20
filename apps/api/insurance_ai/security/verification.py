"""Deterministic identity verification.

Verification is pure application logic. Given claimed factors, it checks them against
the synthetic customer record and requires a minimum number of correct factors. An
LLM is never in this decision path.

Policy: require at least 2 correct identifying factors, one of which must be a strong
factor (policy_number, date_of_birth, or otp_code). This mirrors real KBA/IVR flows.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from insurance_ai.config import get_settings
from insurance_ai.db.models import Customer, Policy
from insurance_ai.domain.enums import VerificationStatus
from insurance_ai.security.session import Session

STRONG_FACTORS = {"policy_number", "date_of_birth", "otp_code"}
WEAK_FACTORS = {"last_name", "zip_code"}


@dataclass
class VerificationClaim:
    last_name: str | None = None
    zip_code: str | None = None
    policy_number: str | None = None
    date_of_birth: str | None = None  # ISO YYYY-MM-DD
    otp_code: str | None = None


@dataclass
class VerificationResult:
    status: VerificationStatus
    customer_id: str | None
    matched_factors: list[str]
    message: str
    attempts_remaining: int


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


async def _resolve_customer(
    session: AsyncSession, claim: VerificationClaim, bound_customer_id: str | None
) -> Customer | None:
    """Find the candidate customer strictly from a strong identifier.

    If the conversation is already bound to a customer, only that customer can be
    verified against (prevents pivoting to another account mid-session).
    """
    if bound_customer_id:
        return await session.get(Customer, bound_customer_id)
    if claim.policy_number:
        row = await session.execute(
            select(Policy).where(Policy.policy_number == claim.policy_number.strip().upper())
        )
        policy = row.scalar_one_or_none()
        if policy:
            return await session.get(Customer, policy.customer_id)
    return None


async def verify_identity(
    db: AsyncSession, sess: Session, claim: VerificationClaim
) -> VerificationResult:
    settings = get_settings()
    sess.attempts += 1
    attempts_remaining = max(0, settings.verification_max_attempts - sess.attempts)

    customer = await _resolve_customer(db, claim, sess.customer_id)
    if customer is None:
        sess.verification_status = (
            VerificationStatus.FAILED if attempts_remaining == 0 else VerificationStatus.IN_PROGRESS
        )
        return VerificationResult(
            status=sess.verification_status,
            customer_id=None,
            matched_factors=[],
            message="Could not locate an account from the details provided.",
            attempts_remaining=attempts_remaining,
        )

    matched: list[str] = []
    if claim.last_name and claim.last_name.strip().lower() == customer.last_name.lower():
        matched.append("last_name")
    if claim.zip_code and claim.zip_code.strip() == customer.zip_code:
        matched.append("zip_code")
    if claim.date_of_birth and _parse_date(claim.date_of_birth) == customer.date_of_birth:
        matched.append("date_of_birth")
    if claim.otp_code and claim.otp_code.strip() == settings.otp_code_for_demo:
        matched.append("otp_code")
    if claim.policy_number:
        # policy number already proven to resolve to this customer above
        matched.append("policy_number")

    strong = [f for f in matched if f in STRONG_FACTORS]
    passed = len(matched) >= 2 and len(strong) >= 1

    if passed:
        sess.verification_status = VerificationStatus.VERIFIED
        sess.customer_id = customer.id
        sess.satisfied_factors = matched
        return VerificationResult(
            status=VerificationStatus.VERIFIED,
            customer_id=customer.id,
            matched_factors=matched,
            message="Identity verified.",
            attempts_remaining=attempts_remaining,
        )

    # Bind the candidate customer so retries can't pivot to another account.
    sess.customer_id = customer.id
    sess.verification_status = (
        VerificationStatus.FAILED if attempts_remaining == 0 else VerificationStatus.IN_PROGRESS
    )
    need = "at least two matching details, including a policy number, date of birth, or code"
    return VerificationResult(
        status=sess.verification_status,
        customer_id=None,
        matched_factors=matched,
        message=(
            "That information didn't fully match. I need "
            + need
            + f". Attempts remaining: {attempts_remaining}."
            if attempts_remaining
            else "Verification failed. I'll connect you with a representative."
        ),
        attempts_remaining=attempts_remaining,
    )
