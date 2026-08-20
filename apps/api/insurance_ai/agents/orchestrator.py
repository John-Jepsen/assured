"""Orchestrator: intent → verification → specialist routing → grounded stream.

Coordinates multiple specialists for multi-intent messages, drives deterministic
identity verification when the user supplies factors, and streams the grounded
answer. Emits a full structured execution trace for the admin dashboard.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from insurance_ai.agents.composer import compose
from insurance_ai.agents.intent import detect
from insurance_ai.agents.specialists import SPECIALISTS
from insurance_ai.config import get_settings
from insurance_ai.domain.enums import AgentName, VerificationStatus
from insurance_ai.providers.base import ChatMessage
from insurance_ai.providers.factory import Providers, get_providers
from insurance_ai.security.session import Session
from insurance_ai.security.verification import VerificationClaim, verify_identity
from insurance_ai.tools.base import Source, ToolContext


@dataclass
class Trace:
    request_id: str
    conversation_id: str | None
    intents: list[str] = field(default_factory=list)
    agents: list[str] = field(default_factory=list)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    sources: list[dict[str, Any]] = field(default_factory=list)
    verification_status: str = "unverified"
    escalated: bool = False
    latencies_ms: dict[str, float] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "conversation_id": self.conversation_id,
            "intents": self.intents,
            "agents": self.agents,
            "tool_calls": self.tool_calls,
            "sources": self.sources,
            "verification_status": self.verification_status,
            "escalated": self.escalated,
            "latencies_ms": self.latencies_ms,
        }


@dataclass
class OrchestratorResult:
    answer: str
    trace: Trace
    needs_verification: bool


def _looks_like_verification(message: str, intent) -> bool:
    ent = intent.entities
    return bool(
        (ent.policy_numbers and ent.zips)
        or ent.dates
        or _has_otp(message)
        or (ent.policy_numbers and _wants_verify(message))
    )


def _has_otp(message: str) -> bool:
    import re

    return bool(re.search(r"\b(code|otp)\b.*\d{4,8}", message.lower()))


def _wants_verify(message: str) -> bool:
    import re

    return bool(re.search(r"verif|here('?s| is) my|my policy (number )?is", message.lower()))


class Orchestrator:
    def __init__(self, providers: Providers | None = None) -> None:
        self.settings = get_settings()
        self.providers = providers or get_providers()

    async def _maybe_verify(
        self, db: AsyncSession, session: Session, message: str, intent, trace: Trace
    ) -> str | None:
        if session.is_verified or not _looks_like_verification(message, intent):
            return None
        import re

        ent = intent.entities
        otp = None
        m = re.search(r"\b(?:code|otp)\b\D*(\d{4,8})", message.lower())
        if m:
            otp = m.group(1)
        claim = VerificationClaim(
            last_name=None,
            zip_code=ent.zips[0] if ent.zips else None,
            policy_number=ent.policy_numbers[0] if ent.policy_numbers else None,
            date_of_birth=ent.dates[0] if ent.dates else None,
            otp_code=otp,
        )
        # naive last-name capture: a capitalized token near "name is"
        name_m = re.search(r"name is ([A-Za-z]+)", message)
        if name_m:
            claim.last_name = name_m.group(1)
        result = await verify_identity(db, session, claim)
        trace.verification_status = session.verification_status
        trace.tool_calls.append({
            "tool_name": "verify_identity", "ok": result.status == VerificationStatus.VERIFIED,
            "arguments": {"factors": result.matched_factors},
            "result_summary": result.message,
        })
        return result.message

    async def run(
        self,
        db: AsyncSession,
        session: Session,
        message: str,
        *,
        request_id: str,
        log_sink=None,
    ) -> OrchestratorResult:
        t0 = time.perf_counter()
        trace = Trace(request_id=request_id, conversation_id=session.conversation_id)
        intent = detect(message)
        trace.intents = intent.intents
        trace.agents = [a.value for a in intent.agents]
        trace.verification_status = session.verification_status

        verify_msg = await self._maybe_verify(db, session, message, intent, trace)

        ctx = ToolContext(
            session=session, db=db, providers=self.providers,
            conversation_id=session.conversation_id, log_sink=log_sink,
        )

        turns = []
        agents = intent.agents or [AgentName.GENERAL]
        for agent_name in agents:
            specialist = SPECIALISTS.get(agent_name)
            if specialist is None:
                continue
            ts = time.perf_counter()
            turn = await specialist.handle(ctx, message, intent)
            trace.latencies_ms[f"agent:{agent_name.value}"] = (time.perf_counter() - ts) * 1000
            for tc in turn.tool_calls:
                trace.tool_calls.append({
                    "tool_name": tc.tool_name, "arguments": tc.arguments, "ok": tc.ok,
                    "error_code": tc.error_code, "result_summary": tc.result_summary,
                    "agent": agent_name.value,
                })
            turns.append(turn)

        draft, sources, needs_verification, escalated = compose(turns, session.is_verified)
        # If we just processed verification, lead with its outcome.
        if verify_msg and (session.is_verified or needs_verification):
            draft = f"{verify_msg} {draft}" if session.is_verified else verify_msg
            if session.is_verified:
                needs_verification = False

        trace.sources = [_source_dict(s) for s in sources]
        trace.escalated = escalated or session.escalated
        trace.verification_status = session.verification_status
        trace.latencies_ms["total_plan_ms"] = (time.perf_counter() - t0) * 1000
        return OrchestratorResult(answer=draft, trace=trace, needs_verification=needs_verification)

    async def stream_answer(self, draft: str, message: str, system_prompt: str) -> AsyncIterator[str]:
        """Stream the grounded draft (mock) or LLM-phrased version (real provider)."""
        if self.settings.llm_provider == "mock":
            msgs = [ChatMessage(role="system", content="GROUNDED_ANSWER:" + draft)]
        else:
            msgs = [
                ChatMessage(role="system", content=system_prompt),
                ChatMessage(
                    role="system",
                    content="Grounded facts — phrase these concisely and add NOTHING else:\n" + draft,
                ),
                ChatMessage(role="user", content=message),
            ]
        async for token in self.providers.llm.stream(msgs):
            yield token


def _source_dict(s: Source) -> dict[str, Any]:
    return {
        "citation": s.citation, "document_id": s.document_id, "chunk_id": s.chunk_id,
        "score": s.score, "snippet": s.snippet,
    }
