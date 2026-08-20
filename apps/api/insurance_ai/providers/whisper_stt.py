"""faster-whisper STT adapter (Whisper family). Requires the `speech` extra.

Near-streaming: audio is transcribed in segments; partials are emitted per segment
and a final transcript on completion. Model size is configurable (tiny..large-v3);
runs on CPU or CUDA per hardware profile.
"""

from __future__ import annotations

import asyncio
import io
from collections.abc import AsyncIterator

from insurance_ai.config import Settings
from insurance_ai.providers.base import (
    SpeechToTextProvider,
    STTResult,
    TranscriptChunk,
)


class FasterWhisperSTT(SpeechToTextProvider):
    name = "faster-whisper"

    def __init__(self, settings: Settings) -> None:
        from faster_whisper import WhisperModel

        device = "cuda" if settings.hardware_profile in ("nvidia", "cloud-gpu") else "cpu"
        compute = "float16" if device == "cuda" else "int8"
        self._model = WhisperModel(settings.stt_model, device=device, compute_type=compute)

    def _decode(self, audio: bytes):
        import soundfile as sf

        data, sr = sf.read(io.BytesIO(audio), dtype="float32")
        return data, sr

    async def transcribe(self, audio: bytes, sample_rate: int = 16000) -> STTResult:
        def _run() -> STTResult:
            data, _sr = self._decode(audio)
            segments, _info = self._model.transcribe(data, beam_size=1, vad_filter=True)
            parts = [s.text for s in segments]
            return STTResult(text=" ".join(p.strip() for p in parts).strip(), partials=parts)

        return await asyncio.to_thread(_run)

    async def stream_transcribe(
        self, audio_stream: AsyncIterator[bytes], sample_rate: int = 16000
    ) -> AsyncIterator[TranscriptChunk]:
        # Buffer then segment-transcribe; emit each segment as a partial, last as final.
        buffer = b""
        async for chunk in audio_stream:
            buffer += chunk
        result = await self.transcribe(buffer, sample_rate)
        acc = ""
        for part in result.partials:
            acc = (acc + " " + part.strip()).strip()
            yield TranscriptChunk(text=acc, is_final=False)
        yield TranscriptChunk(text=result.text, is_final=True)
