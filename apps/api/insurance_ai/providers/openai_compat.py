"""OpenAI-compatible LLM + embedding adapter (also serves local Ollama).

Real implementation using httpx against any OpenAI-compatible ``/chat/completions``
and ``/embeddings`` endpoint. This is the optional external-provider path; the
system runs fully on the mock/local providers without it.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx

from insurance_ai.config import Settings
from insurance_ai.providers.base import ChatMessage, EmbeddingProvider, LLMProvider


class OpenAICompatLLM(LLMProvider):
    name = "openai_compat"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        if settings.llm_provider == "ollama":
            self.base_url = settings.ollama_base_url.rstrip("/") + "/v1"
            self.api_key = "ollama"
        else:
            self.base_url = settings.openai_base_url.rstrip("/")
            self.api_key = settings.openai_api_key or ""
        self.model = settings.llm_model

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    async def complete(self, messages: list[ChatMessage], **kwargs) -> str:
        payload = {
            "model": self.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": kwargs.get("temperature", 0.2),
        }
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(
                f"{self.base_url}/chat/completions", json=payload, headers=self._headers()
            )
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"]

    async def stream(self, messages: list[ChatMessage], **kwargs) -> AsyncIterator[str]:
        payload = {
            "model": self.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": kwargs.get("temperature", 0.2),
            "stream": True,
        }
        async with httpx.AsyncClient(timeout=120) as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                json=payload,
                headers=self._headers(),
            ) as r:
                r.raise_for_status()
                async for line in r.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data = line[6:]
                    if data.strip() == "[DONE]":
                        break
                    import json

                    try:
                        delta = json.loads(data)["choices"][0]["delta"].get("content")
                    except (KeyError, IndexError, ValueError):
                        continue
                    if delta:
                        yield delta


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
