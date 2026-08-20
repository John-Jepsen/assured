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
