"""OpenAI-compatible LLM + embedding adapter (also serves local Ollama).

Real implementation using httpx against any OpenAI-compatible ``/chat/completions``
and ``/embeddings`` endpoint. This is the optional external-provider path; the
system runs fully on the mock/local providers without it.
"""

from __future__ import annotations

import asyncio
import json
import random
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx

from insurance_ai.config import Settings
from insurance_ai.observability.metrics import (
    MODEL_QUEUE_REJECTIONS,
    MODEL_REQUESTS_ACTIVE,
    PROVIDER_REQUEST_DURATION,
    PROVIDER_REQUESTS,
)
from insurance_ai.providers.base import ChatMessage, EmbeddingProvider, LLMMetadata, LLMProvider

_RETRYABLE_STATUS_CODES = {408, 409, 425, 429, 500, 502, 503, 504}


class ModelOverloadedError(RuntimeError):
    """The bounded local inference queue cannot accept another request."""


class ModelCircuitOpenError(RuntimeError):
    """Model calls are paused after repeated endpoint failures."""


class OpenAICompatLLM(LLMProvider):
    """Persistent async client for OpenAI-compatible chat-completions APIs.

    Complete requests are retried on transient transport/status failures. Streaming
    requests are retried only before the first token is emitted, which avoids
    duplicating text after a mid-stream disconnect.
    """

    name = "openai_compat"

    def __init__(
        self,
        settings: Settings,
        *,
        client: httpx.AsyncClient | None = None,
        clock=time.monotonic,
    ) -> None:
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
        self._clock = clock
        self._concurrency = asyncio.Semaphore(settings.llm_max_concurrency)
        self._admission_lock = asyncio.Lock()
        self._admitted = 0
        self._circuit_lock = asyncio.Lock()
        self._circuit_failures = 0
        self._circuit_open_until = 0.0
        self._circuit_probe_in_flight = False
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
            await asyncio.sleep(delay * random.uniform(0.5, 1.5))

    def _can_retry_status(self, response: httpx.Response, attempt: int) -> bool:
        return (
            response.status_code in _RETRYABLE_STATUS_CODES
            and attempt < self.settings.llm_max_retries
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def ready(self) -> bool:
        """Check the OpenAI-compatible model catalog without changing breaker state."""
        started = time.perf_counter()
        outcome = "error"
        if self._circuit_open_until > self._clock():
            PROVIDER_REQUESTS.inc(self.name, "ready", "circuit_open")
            PROVIDER_REQUEST_DURATION.observe(
                time.perf_counter() - started, self.name, "ready"
            )
            return False
        try:
            response = await self._client.get(
                f"{self.base_url}/models",
                headers=self._headers(),
            )
            response.raise_for_status()
            payload = response.json()
            models = payload.get("data", [])
            ready = isinstance(models, list) and any(
                isinstance(model, dict) and model.get("id") == self.model for model in models
            )
            outcome = "success" if ready else "not_ready"
            return ready
        except (httpx.HTTPError, TypeError, ValueError):
            return False
        finally:
            PROVIDER_REQUESTS.inc(self.name, "ready", outcome)
            PROVIDER_REQUEST_DURATION.observe(
                time.perf_counter() - started, self.name, "ready"
            )

    @asynccontextmanager
    async def _admission(self):
        limit = self.settings.llm_max_concurrency + self.settings.llm_max_pending_requests
        async with self._admission_lock:
            if self._admitted >= limit:
                MODEL_QUEUE_REJECTIONS.inc(self.name, "queue_full")
                raise ModelOverloadedError("model request queue is full")
            self._admitted += 1
        acquired = False
        try:
            try:
                await asyncio.wait_for(
                    self._concurrency.acquire(),
                    timeout=self.settings.llm_queue_timeout_seconds,
                )
                acquired = True
            except TimeoutError as exc:
                MODEL_QUEUE_REJECTIONS.inc(self.name, "queue_timeout")
                raise ModelOverloadedError("model request queue timed out") from exc
            MODEL_REQUESTS_ACTIVE.inc(self.name)
            yield
        finally:
            if acquired:
                MODEL_REQUESTS_ACTIVE.dec(self.name)
                self._concurrency.release()
            async with self._admission_lock:
                self._admitted -= 1

    async def _before_model_request(self) -> bool:
        """Return whether this request is the single half-open recovery probe."""
        async with self._circuit_lock:
            now = self._clock()
            if self._circuit_open_until > now:
                MODEL_QUEUE_REJECTIONS.inc(self.name, "circuit_open")
                raise ModelCircuitOpenError("model circuit breaker is open")
            if self._circuit_open_until:
                if self._circuit_probe_in_flight:
                    MODEL_QUEUE_REJECTIONS.inc(self.name, "recovery_probe_in_flight")
                    raise ModelCircuitOpenError("model circuit recovery probe is in flight")
                self._circuit_probe_in_flight = True
                return True
            return False

    async def _record_success(self) -> None:
        async with self._circuit_lock:
            self._circuit_failures = 0
            self._circuit_open_until = 0.0
            self._circuit_probe_in_flight = False

    async def _record_failure(self, recovery_probe: bool) -> None:
        async with self._circuit_lock:
            self._circuit_failures += 1
            if (
                recovery_probe
                or self._circuit_failures >= self.settings.llm_circuit_failure_threshold
            ):
                self._circuit_open_until = (
                    self._clock() + self.settings.llm_circuit_recovery_seconds
                )
            self._circuit_probe_in_flight = False

    async def _release_recovery_probe(self) -> None:
        async with self._circuit_lock:
            self._circuit_probe_in_flight = False

    async def _complete_request(self, payload: dict[str, Any]) -> str:
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

    async def complete(self, messages: list[ChatMessage], **kwargs) -> str:
        payload = self._payload(messages, kwargs)
        started = time.perf_counter()
        outcome = "error"
        try:
            async with self._admission():
                recovery_probe = await self._before_model_request()
                try:
                    result = await self._complete_request(payload)
                except asyncio.CancelledError:
                    outcome = "cancelled"
                    if recovery_probe:
                        await self._record_failure(recovery_probe=True)
                    raise
                except Exception:
                    await self._record_failure(recovery_probe)
                    raise
                await self._record_success()
                outcome = "success"
                return result
        except (ModelOverloadedError, ModelCircuitOpenError):
            outcome = "rejected"
            raise
        finally:
            PROVIDER_REQUESTS.inc(self.name, "complete", outcome)
            PROVIDER_REQUEST_DURATION.observe(
                time.perf_counter() - started, self.name, "complete"
            )

    async def stream(self, messages: list[ChatMessage], **kwargs) -> AsyncIterator[str]:
        payload = {**self._payload(messages, kwargs), "stream": True}
        started = time.perf_counter()
        outcome = "error"
        try:
            async with self._admission():
                recovery_probe = await self._before_model_request()
                try:
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
                                        await self._record_success()
                                        outcome = "success"
                                        return
                                    try:
                                        delta = json.loads(data)["choices"][0]["delta"].get(
                                            "content"
                                        )
                                    except (KeyError, IndexError, TypeError, ValueError):
                                        continue
                                    if delta:
                                        emitted = True
                                        yield delta
                                await self._record_success()
                                outcome = "success"
                                return
                        except httpx.TransportError:
                            if emitted or attempt >= self.settings.llm_max_retries:
                                raise
                            await self._backoff(attempt)
                except asyncio.CancelledError:
                    outcome = "cancelled"
                    if recovery_probe:
                        await self._record_failure(recovery_probe=True)
                    raise
                except Exception:
                    await self._record_failure(recovery_probe)
                    raise
                finally:
                    if recovery_probe:
                        await self._release_recovery_probe()
        except (ModelOverloadedError, ModelCircuitOpenError):
            outcome = "rejected"
            raise
        finally:
            PROVIDER_REQUESTS.inc(self.name, "stream", outcome)
            PROVIDER_REQUEST_DURATION.observe(
                time.perf_counter() - started, self.name, "stream"
            )


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
