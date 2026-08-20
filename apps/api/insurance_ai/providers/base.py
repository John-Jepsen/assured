"""Provider interfaces. Implementations live alongside; the factory selects one."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field


@dataclass
class ChatMessage:
    role: str
    content: str


@dataclass
class TranscriptChunk:
    text: str
    is_final: bool
    start_ms: float = 0.0
    end_ms: float = 0.0


@dataclass
class AudioChunk:
    """A chunk of PCM/16-bit audio (or opaque bytes for mock)."""

    data: bytes
    sample_rate: int = 22050
    is_final: bool = False
    text: str = ""  # the text this audio renders (useful for tests/telephony logs)


@dataclass
class STTResult:
    text: str
    confidence: float = 1.0
    partials: list[str] = field(default_factory=list)


class LLMProvider(ABC):
    """Text generation. Must support streaming; pydantic-ai wraps these for agents."""

    name: str = "base"

    @abstractmethod
    async def complete(self, messages: list[ChatMessage], **kwargs) -> str: ...

    @abstractmethod
    async def stream(self, messages: list[ChatMessage], **kwargs) -> AsyncIterator[str]: ...

    def pydantic_model(self):  # pragma: no cover - overridden where supported
        """Return a pydantic-ai Model, or None to force the rule-based fallback."""
        return


class SpeechToTextProvider(ABC):
    name: str = "base"

    @abstractmethod
    async def transcribe(self, audio: bytes, sample_rate: int = 16000) -> STTResult: ...

    @abstractmethod
    async def stream_transcribe(
        self, audio_stream: AsyncIterator[bytes], sample_rate: int = 16000
    ) -> AsyncIterator[TranscriptChunk]: ...


class TextToSpeechProvider(ABC):
    name: str = "base"

    @abstractmethod
    async def synthesize(self, text: str) -> AudioChunk: ...

    @abstractmethod
    async def stream_synthesize(self, text_stream: AsyncIterator[str]) -> AsyncIterator[AudioChunk]:
        """Incremental synthesis: emit audio per sentence/clause as text arrives."""
        ...


class EmbeddingProvider(ABC):
    name: str = "base"
    dim: int = 256

    @abstractmethod
    async def embed(self, texts: list[str]) -> list[list[float]]: ...

    async def embed_one(self, text: str) -> list[float]:
        return (await self.embed([text]))[0]
