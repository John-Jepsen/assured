"""Grounded rewrite validation and deterministic generation fallback tests."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from insurance_ai.agents.grounding import validate_grounded_rewrite
from insurance_ai.agents.orchestrator import Orchestrator, OrchestratorResult, Trace
from insurance_ai.config import Settings
from insurance_ai.providers.base import ChatMessage, LLMProvider
from insurance_ai.providers.factory import Providers


class StaticLLM(LLMProvider):
    name = "test-rewriter"

    def __init__(self, chunks: list[str] | None = None, error: Exception | None = None) -> None:
        self.chunks = chunks or []
        self.error = error

    async def complete(self, messages: list[ChatMessage], **kwargs) -> str:
        return "".join([token async for token in self.stream(messages, **kwargs)])

    async def stream(self, messages: list[ChatMessage], **kwargs) -> AsyncIterator[str]:
        if self.error:
            raise self.error
        for chunk in self.chunks:
            yield chunk


def _orchestrator(llm: LLMProvider) -> Orchestrator:
    providers = Providers(Settings())
    providers.llm = llm
    return Orchestrator(providers=providers)


def _result(draft: str) -> OrchestratorResult:
    return OrchestratorResult(
        answer=draft,
        trace=Trace(request_id="test", conversation_id="conversation"),
        needs_verification=False,
    )


def test_validator_preserves_numbers_identifiers_urls_citations_and_claims():
    draft = (
        "Policy AUTO-10024 has collision coverage with a $500.00 deductible. "
        "See [Source 1] at https://example.test/policy."
    )
    rendered = (
        "Your collision coverage under policy auto-10024 has a $500 deductible; "
        "details are at [source 1] https://example.test/policy."
    )

    validation = validate_grounded_rewrite(draft, rendered)

    assert validation.valid
    assert all(validation.checks.values())


def test_validator_rejects_changed_or_added_protected_facts():
    draft = "Policy AUTO-10024 has a $500 deductible and is not covered for floods."
    rendered = (
        "Policy AUTO-99999 has a $250 deductible and is covered for floods; "
        "claim processing is available."
    )

    validation = validate_grounded_rewrite(draft, rendered)

    assert not validation.valid
    assert "numbers_preserved" in validation.failures
    assert "numbers_not_added" in validation.failures
    assert "identifiers_preserved" in validation.failures
    assert "identifiers_not_added" in validation.failures
    assert "polarity_preserved" in validation.failures
    assert "critical_claims_not_added" in validation.failures


@pytest.mark.asyncio
async def test_valid_model_rewrite_is_streamed_with_validation_metadata():
    draft = "Policy AUTO-10024 has collision coverage with a $500 deductible."
    rewrite = "Collision coverage on policy AUTO-10024 has a $500 deductible."
    result = _result(draft)

    chunks = [
        token
        async for token in _orchestrator(StaticLLM([rewrite[:20], rewrite[20:]])).stream_result(
            result, "question"
        )
    ]

    assert "".join(chunks) == rewrite
    assert result.trace.generation is not None
    assert result.trace.generation["grounding_validated"] is True
    assert result.trace.generation["fallback_used"] is False


@pytest.mark.asyncio
async def test_invalid_model_rewrite_streams_deterministic_draft_fallback():
    draft = "Policy AUTO-10024 has collision coverage with a $500 deductible."
    unsafe = "Policy AUTO-99999 has collision coverage with a $50 deductible."
    result = _result(draft)

    chunks = [
        token
        async for token in _orchestrator(StaticLLM([unsafe])).stream_result(result, "question")
    ]

    assert "".join(chunks) == draft
    assert result.trace.generation is not None
    assert result.trace.generation["grounding_validated"] is False
    assert result.trace.generation["fallback_used"] is True
    assert "identifiers_preserved" in result.trace.generation["validation_failures"]


@pytest.mark.asyncio
async def test_provider_failure_streams_deterministic_draft_fallback():
    draft = "Policy AUTO-10024 has collision coverage with a $500 deductible."
    result = _result(draft)

    chunks = [
        token
        async for token in _orchestrator(StaticLLM(error=TimeoutError("model timeout"))).stream_result(
            result, "question"
        )
    ]

    assert "".join(chunks) == draft
    assert result.trace.generation == {
        "grounding_validated": False,
        "fallback_used": True,
        "validation_failures": ["provider_error"],
        "checks": {"provider_completed": False},
        "error_type": "TimeoutError",
    }
