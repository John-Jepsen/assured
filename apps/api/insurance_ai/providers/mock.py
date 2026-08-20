"""Deterministic, offline provider implementations.

These make the entire multimodal pipeline runnable and testable with zero model
downloads and no GPU. They are also the ``local-cpu`` degradation profile:

* MockLLM composes a grounded answer from *verified* tool results + retrieved
  sources. It never invents facts — that is a guardrail feature, not a shortcut.
* HashEmbedding is a deterministic lexical hashing vectorizer (real retrieval).
* MockSTT / MockTTS exercise the full streaming + barge-in machinery. MockTTS
  emits valid WAV audio (a tone) and is clearly labelled as placeholder audio in
  the UI; configure Piper for natural speech.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import math
import re
import struct
import wave
from collections.abc import AsyncIterator

from insurance_ai.providers.base import (
    AudioChunk,
    ChatMessage,
    EmbeddingProvider,
    LLMProvider,
    SpeechToTextProvider,
    STTResult,
    TextToSpeechProvider,
    TranscriptChunk,
)

_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Dropping high-frequency words keeps lexical similarity meaningful: an off-topic
# question shares only stopwords with the corpus and correctly scores near zero,
# so RAG returns nothing rather than a spurious match.
_STOPWORDS = frozenset(
    "the a an of to in on for and or is are was were be been being do does did i "
    "you my your me we our it its this that these those what when where how why who "
    "with at by from as if then than so about into over under can could would should "
    "will shall may might have has had not no yes get got".split()
)


def _tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN_RE.findall(text.lower()) if t not in _STOPWORDS and len(t) > 1]


class HashEmbedding(EmbeddingProvider):
    """Deterministic hashing vectorizer → lexical similarity. Offline, no deps."""

    name = "hash"

    def __init__(self, dim: int = 256) -> None:
        self.dim = dim

    async def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            vec = [0.0] * self.dim
            tokens = _tokenize(text)
            for tok in tokens:
                h = int(hashlib.md5(tok.encode()).hexdigest(), 16)
                idx = h % self.dim
                sign = 1.0 if (h >> 8) & 1 else -1.0
                vec[idx] += sign
            norm = math.sqrt(sum(v * v for v in vec)) or 1.0
            vectors.append([v / norm for v in vec])
        return vectors


class MockLLM(LLMProvider):
    """Composes a grounded response from the structured context it is handed.

    The agent layer packs verified facts (tool results) and retrieved sources into
    the final user turn. This provider surfaces those facts verbatim, so the demo
    answers are correct and non-hallucinated even with no real model present.
    """

    name = "mock"

    async def complete(self, messages: list[ChatMessage], **kwargs) -> str:
        return "".join([c async for c in self.stream(messages, **kwargs)])

    async def stream(self, messages: list[ChatMessage], **kwargs) -> AsyncIterator[str]:
        # The orchestrator passes a pre-composed grounded answer as a system directive
        # keyed by "GROUNDED_ANSWER:"; we stream it token-by-token to exercise streaming.
        answer = ""
        for m in reversed(messages):
            if m.role == "system" and m.content.startswith("GROUNDED_ANSWER:"):
                answer = m.content[len("GROUNDED_ANSWER:") :].strip()
                break
        if not answer:
            answer = messages[-1].content if messages else ""
        for token in re.findall(r"\S+\s*", answer):
            await asyncio.sleep(0)  # yield control; real streaming cadence
            yield token


class MockSTT(SpeechToTextProvider):
    """Recovers transcript text embedded in the mock audio envelope.

    Mock audio produced by the browser test harness / MockTTS carries its text in a
    trailing marker so the pipeline (VAD → STT → agent) is exercised deterministically.
    """

    name = "mock"
    _MARKER = b"\x00TXT:"

    async def transcribe(self, audio: bytes, sample_rate: int = 16000) -> STTResult:
        text = self._extract(audio)
        return STTResult(text=text, confidence=0.99 if text else 0.0)

    async def stream_transcribe(
        self, audio_stream: AsyncIterator[bytes], sample_rate: int = 16000
    ) -> AsyncIterator[TranscriptChunk]:
        buffer = b""
        async for chunk in audio_stream:
            buffer += chunk
            text = self._extract(buffer)
            if text:
                words = text.split()
                # emit progressive partials
                for i in range(1, len(words)):
                    yield TranscriptChunk(text=" ".join(words[:i]), is_final=False)
                yield TranscriptChunk(text=text, is_final=True)
                return
        yield TranscriptChunk(text=self._extract(buffer), is_final=True)

    def _extract(self, audio: bytes) -> str:
        idx = audio.rfind(self._MARKER)
        if idx == -1:
            return ""
        return audio[idx + len(self._MARKER) :].decode("utf-8", "ignore").strip()

    @staticmethod
    def encode(text: str, base_frames: int = 1600) -> bytes:
        """Test helper: build mock audio bytes carrying `text`."""
        return b"\x00" * base_frames + MockSTT._MARKER + text.encode("utf-8")


class MockTTS(TextToSpeechProvider):
    """Emits valid WAV audio (a low tone) sized to the text. Placeholder speech."""

    name = "mock"

    def __init__(self, sample_rate: int = 22050) -> None:
        self.sample_rate = sample_rate

    async def synthesize(self, text: str) -> AudioChunk:
        return AudioChunk(
            data=self._tone_wav(text), sample_rate=self.sample_rate, is_final=True, text=text
        )

    async def stream_synthesize(
        self, text_stream: AsyncIterator[str]
    ) -> AsyncIterator[AudioChunk]:
        # Incremental: synthesize per sentence as text streams in.
        buffer = ""
        async for token in text_stream:
            buffer += token
            while True:
                m = re.search(r"[.!?]\s", buffer)
                if not m:
                    break
                sentence, buffer = buffer[: m.end()], buffer[m.end() :]
                if sentence.strip():
                    yield AudioChunk(
                        data=self._tone_wav(sentence),
                        sample_rate=self.sample_rate,
                        text=sentence.strip(),
                    )
        if buffer.strip():
            yield AudioChunk(
                data=self._tone_wav(buffer), sample_rate=self.sample_rate, text=buffer.strip()
            )
        yield AudioChunk(data=b"", sample_rate=self.sample_rate, is_final=True)

    def _tone_wav(self, text: str) -> bytes:
        duration = min(3.0, 0.06 * max(1, len(text.split())))
        n = int(self.sample_rate * duration)
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(self.sample_rate)
            frames = bytearray()
            for i in range(n):
                sample = int(3000 * math.sin(2 * math.pi * 180 * i / self.sample_rate))
                frames += struct.pack("<h", sample)
            w.writeframes(bytes(frames))
        return buf.getvalue()
