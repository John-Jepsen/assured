"""Orchestrator behavior: routing, verification flow, multi-intent, grounding."""

from __future__ import annotations

import pytest

from insurance_ai.agents.intent import detect
from insurance_ai.agents.orchestrator import Orchestrator
from insurance_ai.domain.enums import AgentName
from insurance_ai.security.verification import VerificationClaim, verify_identity
from insurance_ai.tools.registry import load_all_tools

load_all_tools()


async def _run(orch, db, sess, msg):
    return await orch.run(db, sess, msg, request_id="rid")


async def _verify(db, sess):
    await verify_identity(db, sess, VerificationClaim(policy_number="AUTO-10024", zip_code="78258"))


def test_intent_routes_to_correct_agent():
    assert AgentName.BILLING in detect("when is my next payment?").agents
    assert AgentName.CLAIMS in detect("what's the status of my accident claim?").agents
    assert AgentName.POLICY in detect("does my policy cover a rental car?").agents


def test_multi_intent_detected():
    result = detect("what's my claim status CLAIM-90001 and when is my next payment?")
    assert AgentName.CLAIMS in result.agents
    assert AgentName.BILLING in result.agents
    assert "CLAIM-90001" in result.entities.claim_numbers


@pytest.mark.asyncio
async def test_unverified_policy_question_prompts_for_verification(db, new_session):
    orch = Orchestrator()
    res = await _run(orch, db, new_session(), "What is my collision deductible on AUTO-10024?")
    assert res.needs_verification
    assert "verify" in res.answer.lower()


@pytest.mark.asyncio
async def test_general_coverage_question_is_grounded_without_verification(db, new_session):
    orch = Orchestrator()
    res = await _run(orch, db, new_session(), "Does auto insurance usually cover a rental car?")
    # Grounded answer cites the rental reimbursement doc; no verification needed.
    assert res.trace.sources, "expected RAG sources"
    assert "rental" in res.answer.lower()
    assert not res.needs_verification


@pytest.mark.asyncio
async def test_verified_deductible_answer_is_correct_and_sourced(db, new_session):
    orch = Orchestrator()
    sess = new_session()
    await _verify(db, sess)
    res = await _run(orch, db, sess, "What is my collision deductible on AUTO-10024?")
    assert "500" in res.answer  # Maria's collision deductible is $500
    assert any("AUTO-10024" in s["citation"] for s in res.trace.sources)


@pytest.mark.asyncio
async def test_inline_verification_then_answer(db, new_session):
    orch = Orchestrator()
    sess = new_session()
    # Message supplies verification factors + the question is a follow-up turn.
    res = await _run(orch, db, sess, "My policy number is AUTO-10024 and my ZIP is 78258.")
    assert sess.is_verified
    assert "verified" in res.answer.lower()


def test_multi_intent_survives_generic_question_words():
    # Natural multi-part phrasing: the generic "what does / how do i / what is" words
    # must not crowd out the real policy + claim + billing specialists.
    result = detect(
        "What does auto liability cover, and how do I file a claim, "
        "and what is my balance on AUTO-10024?"
    )
    assert AgentName.POLICY in result.agents
    assert AgentName.CLAIMS in result.agents
    assert AgentName.BILLING in result.agents


def test_terminology_still_reaches_general():
    # A pure terminology question with no account context keeps the general agent.
    assert AgentName.GENERAL in detect("What does deductible mean?").agents


@pytest.mark.asyncio
async def test_payment_confirmation_completes_across_turns(db, new_session):
    orch = Orchestrator()
    sess = new_session()
    await _verify(db, sess)
    # Turn 1: asks to confirm, remembers the pending charge — does not charge yet.
    ask = await _run(orch, db, sess, "Pay invoice INV-AUTO-10024-07.")
    assert "confirm" in ask.answer.lower()
    assert sess.pending_payment and sess.pending_payment["invoice_number"] == "INV-AUTO-10024-07"
    # Turn 2: a bare "yes" completes that exact payment via the billing agent.
    done = await _run(orch, db, sess, "yes")
    assert "billing" in done.trace.agents
    assert any(t["tool_name"] == "make_payment" and t["ok"] for t in done.trace.tool_calls)
    assert sess.pending_payment is None


@pytest.mark.asyncio
async def test_payment_decline_cancels_pending(db, new_session):
    orch = Orchestrator()
    sess = new_session()
    await _verify(db, sess)
    await _run(orch, db, sess, "Pay invoice INV-AUTO-10024-07.")
    res = await _run(orch, db, sess, "no")
    assert sess.pending_payment is None
    assert "won't process" in res.answer.lower()
    # The invoice was never charged.
    assert not any(t["tool_name"] == "make_payment" for t in res.trace.tool_calls)


@pytest.mark.asyncio
async def test_politeness_never_auto_charges(db, new_session):
    orch = Orchestrator()
    sess = new_session()
    await _verify(db, sess)
    # "please" must not confirm the charge — it only asks and waits for a real "yes".
    res = await _run(orch, db, sess, "Please pay invoice INV-AUTO-10024-07.")
    assert "confirm" in res.answer.lower()
    assert sess.pending_payment is not None
    make_payment_calls = [t for t in res.trace.tool_calls if t["tool_name"] == "make_payment"]
    assert make_payment_calls and make_payment_calls[0]["arguments"].get("confirm") is False


@pytest.mark.asyncio
async def test_loose_affirmative_word_does_not_charge(db, new_session):
    # "sure, but first, what is the balance?" contains "sure" but is NOT a bare yes —
    # it must never be treated as confirming the pending payment.
    orch = Orchestrator()
    sess = new_session()
    await _verify(db, sess)
    await _run(orch, db, sess, "Pay invoice INV-AUTO-10024-07.")
    res = await _run(orch, db, sess, "sure, but first, what is my balance on AUTO-10024?")
    assert not any(
        t["tool_name"] == "make_payment" and t["arguments"].get("confirm")
        for t in res.trace.tool_calls
    )
    # The pending charge expires once the caller moves on without a bare yes/no.
    assert sess.pending_payment is None


@pytest.mark.asyncio
async def test_stale_pending_payment_expires_before_later_yes(db, new_session):
    # A "yes" on a later, unrelated turn must not complete a stale charge.
    orch = Orchestrator()
    sess = new_session()
    await _verify(db, sess)
    await _run(orch, db, sess, "Pay invoice INV-AUTO-10024-07.")
    # Turn 2 pivots topic without answering → pending expires.
    await _run(orch, db, sess, "What does my auto policy cover?")
    assert sess.pending_payment is None
    # Turn 3 affirms an unrelated point → no charge, because nothing is pending.
    res = await _run(orch, db, sess, "yes, thanks")
    assert not any(t["tool_name"] == "make_payment" for t in res.trace.tool_calls)


@pytest.mark.asyncio
async def test_affirmative_with_other_intent_preserves_that_intent(db, new_session):
    # "yes — actually, I want to speak to a human" must escalate, not silently charge.
    orch = Orchestrator()
    sess = new_session()
    await _verify(db, sess)
    await _run(orch, db, sess, "Pay invoice INV-AUTO-10024-07.")
    res = await _run(orch, db, sess, "yes — actually, I want to speak to a human.")
    assert res.trace.escalated
    assert any(t["tool_name"] == "transfer_to_human" for t in res.trace.tool_calls)
    assert not any(t["tool_name"] == "make_payment" for t in res.trace.tool_calls)


@pytest.mark.asyncio
async def test_multi_intent_coordination(db, new_session):
    orch = Orchestrator()
    sess = new_session()
    await _verify(db, sess)
    res = await _run(
        orch, db, sess,
        "What's the status of claim CLAIM-90001 and my billing balance on AUTO-10024?",
    )
    assert "claims" in res.trace.agents and "billing" in res.trace.agents
    tools = {t["tool_name"] for t in res.trace.tool_calls}
    assert "get_claim_status" in tools
    assert "get_billing_status" in tools


@pytest.mark.asyncio
async def test_unknown_question_does_not_invent(db, new_session):
    orch = Orchestrator()
    res = await _run(orch, db, new_session(), "What is the airspeed velocity of a swallow?")
    assert "don't have enough" in res.answer.lower() or "couldn't find" in res.answer.lower()
    assert not res.trace.sources


@pytest.mark.asyncio
async def test_escalation_creates_ticket(db, new_session):
    orch = Orchestrator()
    res = await _run(orch, db, new_session(), "This is unacceptable, I want to speak to a human.")
    assert res.trace.escalated
    assert any(t["tool_name"] == "transfer_to_human" for t in res.trace.tool_calls)
    assert "SUPPORT-" in res.answer


@pytest.mark.asyncio
async def test_streaming_yields_tokens(db, new_session):
    orch = Orchestrator()
    res = await _run(orch, db, new_session(), "Does auto insurance cover a rental car?")
    tokens = [t async for t in orch.stream_answer(res.answer, "q", "sys")]
    assert len(tokens) > 3
    assert "".join(tokens).strip() == res.answer.strip()
