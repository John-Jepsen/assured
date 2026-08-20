"""Runtime selection of provider implementations from configuration.

Real model adapters (faster-whisper, Piper, Ollama/OpenAI, sentence-transformers)
are imported lazily so the deterministic core and its tests never require heavy
ML dependencies. Selecting a real provider without its extra installed raises a
clear, actionable error at startup.
"""

from __future__ import annotations

from functools import lru_cache

from insurance_ai.config import Settings, get_settings
from insurance_ai.providers.base import (
    EmbeddingProvider,
    LLMProvider,
    SpeechToTextProvider,
    TextToSpeechProvider,
)
from insurance_ai.providers.mock import HashEmbedding, MockLLM, MockSTT, MockTTS


def _missing(extra: str, provider: str) -> ImportError:
    return ImportError(
        f"Provider '{provider}' requires the '{extra}' extra. "
        f"Install with: pip install -e '.[{extra}]'  (or use the Docker profile)."
    )


def build_llm(settings: Settings) -> LLMProvider:
    if settings.llm_provider == "mock":
        return MockLLM()
    if settings.llm_provider in ("openai", "ollama"):
        from insurance_ai.providers.openai_compat import OpenAICompatLLM

        return OpenAICompatLLM(settings)
    if settings.llm_provider == "huggingface":
        try:
            from insurance_ai.providers.hf_llm import HFTransformersLLM
        except ImportError as e:  # pragma: no cover
            raise _missing("speech", "huggingface") from e
        return HFTransformersLLM(settings)
    raise ValueError(f"Unknown llm_provider: {settings.llm_provider}")


def build_stt(settings: Settings) -> SpeechToTextProvider:
    if settings.stt_provider == "mock":
        return MockSTT()
    if settings.stt_provider == "faster-whisper":
        try:
            from insurance_ai.providers.whisper_stt import FasterWhisperSTT
        except ImportError as e:  # pragma: no cover
            raise _missing("speech", "faster-whisper") from e
        return FasterWhisperSTT(settings)
    raise ValueError(f"Unknown stt_provider: {settings.stt_provider}")


def build_tts(settings: Settings) -> TextToSpeechProvider:
    if settings.tts_provider == "mock":
        return MockTTS()
    if settings.tts_provider in ("piper", "kokoro"):
        try:
            from insurance_ai.providers.piper_tts import PiperTTS
        except ImportError as e:  # pragma: no cover
            raise _missing("speech", settings.tts_provider) from e
        return PiperTTS(settings)
    raise ValueError(f"Unknown tts_provider: {settings.tts_provider}")


def build_embedding(settings: Settings) -> EmbeddingProvider:
    if settings.embedding_provider in ("mock", "hash"):
        return HashEmbedding(dim=settings.embedding_dim)
    if settings.embedding_provider == "sentence-transformers":
        try:
            from insurance_ai.providers.st_embedding import SentenceTransformerEmbedding
        except ImportError as e:  # pragma: no cover
            raise _missing("speech", "sentence-transformers") from e
        return SentenceTransformerEmbedding(settings)
    if settings.embedding_provider == "openai":
        from insurance_ai.providers.openai_compat import OpenAIEmbedding

        return OpenAIEmbedding(settings)
    raise ValueError(f"Unknown embedding_provider: {settings.embedding_provider}")


class Providers:
    """Bundle of the active providers, resolved once at startup."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.llm = build_llm(settings)
        self.stt = build_stt(settings)
        self.tts = build_tts(settings)
        self.embedding = build_embedding(settings)


@lru_cache
def get_providers() -> Providers:
    return Providers(get_settings())
