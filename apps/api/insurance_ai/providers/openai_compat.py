"""OpenAI-compatible LLM + embedding adapter (also serves local Ollama).

Real implementation using httpx against any OpenAI-compatible ``/chat/completions``
and ``/embeddings`` endpoint. This is the optional external-provider path; the
system runs fully on the mock/local providers without it.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from insurance_ai.config import Settings
from insurance_ai.providers.base import ChatMessage, EmbeddingProvider, LLMMetadata, LLMProvider

_RETRYABLE_STATUS_CODES = {408, 409, 425, 429, 500, 502, 503, 504}


class OpenAICompatLLM(LLMProvider):
    """Persistent async client for OpenAI-compatible chat-completions APIs.

    Complete requests are retried on transient transport/status failures. Streaming
    requests are retried only before the first token is emitted, which avoids
    duplicating text after a mid-stream disconnect.
    """

    name = "openai_compat"

    def __init__(self, settings: Settings, *, client: httpx.AsyncClient | None = None) -> None:
        self.settings = settings
        if settings.llm_provider == "ollama":
            self.base_url = settings.ollama_base_url.rstrip("/") + "/v1"
            self.api_key = "ollama"
            provider_name = "ollama"
            revision = None
        elif settings.llm_provider == "huggingface":
            self.base_url = settings.huggingface_base_url.rstrip("/")
            self.api_key = settings.huggingface_token or ""
            provider_name = "huggingface"
            revision = settings.huggingface_model_revision
        else:
            self.base_url = settings.openai_base_url.rstrip("/")
            self.api_key = settings.openai_api_key or ""
            provider_name = "openai"
            revision = None
        self.model = settings.llm_model
        self.name = provider_name
        self.metadata = LLMMetadata(
            provider=provider_name,
            model=self.model,
            revision=revision,
            endpoint=self.base_url,
        )
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(
                settings.llm_request_timeout_seconds,
                connect=settings.llm_connect_timeout_seconds,
            ),
            limits=httpx.Limits(
                max_connections=settings.llm_max_connections,
                max_keepalive_connections=settings.llm_max_keepalive_connections,
            ),
        )

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _payload(self, messages: list[ChatMessage], kwargs: dict[str, Any]) -> dict[str, Any]:
        return {
            "model": self.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": kwargs.get("temperature", 0.2),
            "max_tokens": kwargs.get("max_tokens", self.settings.llm_max_tokens),
        }

    async def _backoff(self, attempt: int) -> None:
        delay = self.settings.llm_retry_backoff_seconds * (2**attempt)
        if delay > 0:
            await asyncio.sleep(delay)

    def _can_retry_status(self, response: httpx.Response, attempt: int) -> bool:
        return (
            response.status_code in _RETRYABLE_STATUS_CODES
            and attempt < self.settings.llm_max_retries
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def complete(self, messages: list[ChatMessage], **kwargs) -> str:
        payload = self._payload(messages, kwargs)
        for attempt in range(self.settings.llm_max_retries + 1):
            try:
                response = await self._client.post(
                    f"{self.base_url}/chat/completions",
                    json=payload,
                    headers=self._headers(),
                )
                if self._can_retry_status(response, attempt):
                    await self._backoff(attempt)
                    continue
                response.raise_for_status()
                return response.json()["choices"][0]["message"]["content"]
            except httpx.TransportError:
                if attempt >= self.settings.llm_max_retries:
                    raise
                await self._backoff(attempt)
        raise RuntimeError("LLM request exhausted retries")  # pragma: no cover

    async def stream(self, messages: list[ChatMessage], **kwargs) -> AsyncIterator[str]:
        payload = {**self._payload(messages, kwargs), "stream": True}
        for attempt in range(self.settings.llm_max_retries + 1):
            emitted = False
            try:
                async with self._client.stream(
                    "POST",
                    f"{self.base_url}/chat/completions",
                    json=payload,
                    headers=self._headers(),
                ) as response:
                    if self._can_retry_status(response, attempt):
                        await self._backoff(attempt)
                        continue
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            return
                        try:
                            delta = json.loads(data)["choices"][0]["delta"].get("content")
                        except (KeyError, IndexError, TypeError, ValueError):
                            continue
                        if delta:
                            emitted = True
                            yield delta
                    return
            except httpx.TransportError:
                if emitted or attempt >= self.settings.llm_max_retries:
                    raise
                await self._backoff(attempt)


class OpenAIEmbedding(EmbeddingProvider):
    name = "openai"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.base_url = settings.openai_base_url.rstrip("/")
        self.api_key = settings.openai_api_key or ""
        self.model = settings.embedding_model
        self.dim = settings.embedding_dim

    async def embed(self, texts: list[str]) -> list[list[float]]:
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(
                f"{self.base_url}/embeddings",
                json={"model": self.model, "input": texts},
                headers={"Authorization": f"Bearer {self.api_key}"},
            )
            r.raise_for_status()
            return [item["embedding"] for item in r.json()["data"]]


class OllamaEmbedding(OpenAIEmbedding):
    """Embeddings from a local Ollama server via its OpenAI-compatible endpoint.

    Enables real semantic retrieval (e.g. nomic-embed-text) with zero API keys — the
    "easy real models" Docker profile uses this alongside an Ollama LLM.
    """

    name = "ollama"

    def __init__(self, settings) -> None:
        super().__init__(settings)
        self.base_url = settings.ollama_base_url.rstrip("/") + "/v1"
        self.api_key = "ollama"
