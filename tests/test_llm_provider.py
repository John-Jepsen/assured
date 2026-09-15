"""Focused tests for remote OpenAI-compatible Hugging Face model serving."""

from __future__ import annotations

import json

import httpx
import pytest
from insurance_ai.config import Settings
from insurance_ai.providers.base import ChatMessage
from insurance_ai.providers.factory import build_llm
from insurance_ai.providers.openai_compat import OpenAICompatLLM
from pydantic import ValidationError


def _settings(**overrides) -> Settings:
    values = {
        "llm_provider": "huggingface",
        "llm_model": "Qwen/Qwen2.5-1.5B-Instruct",
        "huggingface_base_url": "http://model.internal:8000/v1/",
        "huggingface_token": "test-model-key",
        "huggingface_model_revision": "989aa7980e4cf806f80c7fef2b1adb7bc71aa306",
        "llm_max_retries": 0,
        "llm_retry_backoff_seconds": 0,
    }
    values.update(overrides)
    return Settings(**values)


@pytest.mark.asyncio
async def test_huggingface_provider_uses_remote_endpoint_and_exposes_metadata():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "Grounded response"}}]},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatLLM(_settings(), client=client)
        result = await provider.complete(
            [ChatMessage(role="user", content="What is covered?")],
            temperature=0.1,
            max_tokens=64,
        )

    assert result == "Grounded response"
    assert provider.metadata is not None
    assert provider.metadata.provider == "huggingface"
    assert provider.metadata.model == "Qwen/Qwen2.5-1.5B-Instruct"
    assert provider.metadata.revision == "989aa7980e4cf806f80c7fef2b1adb7bc71aa306"
    assert provider.metadata.endpoint == "http://model.internal:8000/v1"
    assert requests[0].url == "http://model.internal:8000/v1/chat/completions"
    assert requests[0].headers["authorization"] == "Bearer test-model-key"
    payload = json.loads(requests[0].content)
    assert payload["max_tokens"] == 64
    assert payload["messages"] == [{"role": "user", "content": "What is covered?"}]


@pytest.mark.asyncio
async def test_complete_retries_transient_status_with_a_bounded_attempt_count():
    attempts = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            return httpx.Response(503, json={"error": "warming"})
        return httpx.Response(200, json={"choices": [{"message": {"content": "ready"}}]})

    settings = _settings(llm_max_retries=2)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatLLM(settings, client=client)
        result = await provider.complete([ChatMessage(role="user", content="Hello")])

    assert result == "ready"
    assert attempts == 3


@pytest.mark.asyncio
async def test_stream_parses_sse_chunks_and_done_marker():
    body = (
        'data: {"choices":[{"delta":{"content":"Hello"}}]}\n\n'
        'data: {"choices":[{"delta":{"content":" there"}}]}\n\n'
        "data: [DONE]\n\n"
    )

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatLLM(_settings(), client=client)
        chunks = [
            chunk
            async for chunk in provider.stream([ChatMessage(role="user", content="Hello")])
        ]

    assert chunks == ["Hello", " there"]


@pytest.mark.asyncio
async def test_factory_selects_remote_provider_without_transformers_dependency():
    provider = build_llm(_settings())
    try:
        assert isinstance(provider, OpenAICompatLLM)
        assert provider.name == "huggingface"
    finally:
        await provider.aclose()


def test_llm_transport_limits_are_validated():
    with pytest.raises(ValidationError, match="llm_max_keepalive_connections"):
        _settings(llm_max_connections=1, llm_max_keepalive_connections=2)
