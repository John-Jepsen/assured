"""Piper TTS adapter. Requires the `speech` extra (`piper-tts`).

Piper is a fast, permissively-licensed, CPU-friendly neural TTS. The voice model is
auto-downloaded on first use into a mounted cache (``PIPER_VOICE_DIR``, default
``/models/piper``) so containers "just work" and never re-download. Synthesis is
per-sentence, so audio streams incrementally as the LLM produces text.
"""

from __future__ import annotations

import asyncio
import io
import re
import wave
from collections.abc import AsyncIterator
from pathlib import Path

from insurance_ai.config import Settings
from insurance_ai.observability import get_logger
from insurance_ai.providers.base import AudioChunk, TextToSpeechProvider

log = get_logger("piper")


class PiperTTS(TextToSpeechProvider):
    name = "piper"

    def __init__(self, settings: Settings) -> None:
        import os

        from piper import PiperVoice
        from piper.download_voices import download_voice

        voice_id = settings.tts_voice
        cache = Path(os.environ.get("PIPER_VOICE_DIR", "/models/piper"))
        cache.mkdir(parents=True, exist_ok=True)
        onnx = cache / f"{voice_id}.onnx"
        if not onnx.exists():
            log.info("piper_download_voice", voice=voice_id, dir=str(cache))
            download_voice(voice_id, cache)  # fetches {voice}.onnx + .onnx.json
        self._voice = PiperVoice.load(onnx)
        self.sample_rate = self._voice.config.sample_rate

    def _synth_bytes(self, text: str) -> bytes:
        chunks = list(self._voice.synthesize(text))
        if not chunks:
            return b""
        first = chunks[0]
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(first.sample_channels)
            w.setsampwidth(first.sample_width)
            w.setframerate(first.sample_rate)
            w.writeframes(b"".join(c.audio_int16_bytes for c in chunks))
        return buf.getvalue()

    async def synthesize(self, text: str) -> AudioChunk:
        data = await asyncio.to_thread(self._synth_bytes, text)
        return AudioChunk(data=data, sample_rate=self.sample_rate, is_final=True, text=text)

    async def stream_synthesize(self, text_stream: AsyncIterator[str]) -> AsyncIterator[AudioChunk]:
        buffer = ""
        async for token in text_stream:
            buffer += token
            while True:
                m = re.search(r"[.!?]\s", buffer)
                if not m:
                    break
                sentence, buffer = buffer[: m.end()].strip(), buffer[m.end() :]
                if sentence:
                    data = await asyncio.to_thread(self._synth_bytes, sentence)
                    yield AudioChunk(data=data, sample_rate=self.sample_rate, text=sentence)
        if buffer.strip():
            data = await asyncio.to_thread(self._synth_bytes, buffer.strip())
            yield AudioChunk(data=data, sample_rate=self.sample_rate, text=buffer.strip())
        yield AudioChunk(data=b"", sample_rate=self.sample_rate, is_final=True)
