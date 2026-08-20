"""Security behavior: verification enforcement + cross-customer authorization.

These assert OUTCOMES the spec's Definition of Done requires, not implementation.
"""

from __future__ import annotations

import pytest

from insurance_ai.security.authorization import AuthorizationError
from insurance_ai.security.verification import VerificationClaim, verify_identity
from insurance_ai.tools.registry import get_tool, load_all_tools
from insurance_ai.tools.base import ToolContext

load_all_tools()


async def _verify_maria(db, sess):
    return await verify_identity(
        db, sess, VerificationClaim(policy_number="AUTO-10024", zip_code="78258")
    )


@pytest.mark.asyncio
async def test_unverified_user_cannot_read_policy(db, new_session):
    sess = new_session()
    ctx = ToolContext(session=sess, db=db)
    result = await get_tool("lookup_policy").run(ctx, {"policy_number": "AUTO-10024"})
    assert result.ok is False
    assert result.error_code == "not_verified"
    # No policy data leaked.
    assert "premium_amount" not in result.data


@pytest.mark.asyncio
async def test_verification_requires_two_factors_with_a_strong_one(db, new_session):
    sess = new_session()
    # ZIP alone (weak only) must not verify.
    r = await verify_identity(db, sess, VerificationClaim(zip_code="78258"))
    assert r.status.value != "verified"

    sess2 = new_session()
    r2 = await _verify_maria(db, sess2)
    assert r2.status.value == "verified"
    assert sess2.is_verified


@pytest.mark.asyncio
async def test_verified_user_reads_own_policy(db, new_session):
    sess = new_session()
    await _verify_maria(db, sess)
    ctx = ToolContext(session=sess, db=db)
    result = await get_tool("lookup_policy").run(ctx, {"policy_number": "AUTO-10024"})
    assert result.ok
    assert result.data["policy_number"] == "AUTO-10024"
    assert result.data["product_type"] == "auto"


@pytest.mark.asyncio
async def test_cross_customer_access_is_forbidden(db, new_session):
    # Maria is verified but tries to read James's policy AUTO-10025.
    sess = new_session()
    await _verify_maria(db, sess)
    ctx = ToolContext(session=sess, db=db)
    result = await get_tool("lookup_policy").run(ctx, {"policy_number": "AUTO-10025"})
    assert result.ok is False
    assert result.error_code == "forbidden"
    assert "premium_amount" not in result.data


@pytest.mark.asyncio
async def test_cross_customer_claim_access_forbidden(db, new_session):
    sess = new_session()
    await _verify_maria(db, sess)
    ctx = ToolContext(session=sess, db=db)
    # CLAIM-90003 belongs to Dana Lee, not Maria.
    result = await get_tool("get_claim_status").run(ctx, {"claim_number": "CLAIM-90003"})
    assert result.ok is False
    assert result.error_code == "forbidden"


@pytest.mark.asyncio
async def test_verification_locks_out_after_max_attempts(db, new_session):
    sess = new_session()
    for _ in range(3):
        await verify_identity(db, sess, VerificationClaim(policy_number="AUTO-10024", zip_code="00000"))
    assert sess.verification_status.value == "failed"


@pytest.mark.asyncio
async def test_cannot_pivot_to_another_account_mid_session(db, new_session):
    # Start verifying against Maria (bind), then try James's number — stays bound to Maria.
    sess = new_session()
    await verify_identity(db, sess, VerificationClaim(policy_number="AUTO-10024", zip_code="00000"))
    bound = sess.customer_id
    await verify_identity(db, sess, VerificationClaim(policy_number="AUTO-10025", zip_code="60614"))
    assert sess.customer_id == bound  # did not pivot
    assert not sess.is_verified
