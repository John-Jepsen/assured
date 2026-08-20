"""Piper TTS adapter. Requires the `speech` extra + a downloaded Piper voice.

Piper is a fast, permissively-licensed, CPU-friendly neural TTS. This adapter
synthesizes per sentence so audio can stream incrementally as the LLM produces text.
Kokoro can be swapped in behind the same interface (config tts_provider=kokoro).
"""

from __future__ import annotations

import asyncio
import io
import re
import wave
from collections.abc import AsyncIterator

from insurance_ai.config import Settings
from insurance_ai.providers.base import AudioChunk, TextToSpeechProvider


class PiperTTS(TextToSpeechProvider):
    name = "piper"

    def __init__(self, settings: Settings) -> None:
        from piper.voice import PiperVoice  # type: ignore

        # Voice model path is resolved from a mounted model cache by tts_voice id.
        self._voice = PiperVoice.load(self._resolve_voice(settings.tts_voice))
        self.sample_rate = self._voice.config.sample_rate

    @staticmethod
    def _resolve_voice(voice_id: str) -> str:
        import os

        cache = os.environ.get("PIPER_VOICE_DIR", "/models/piper")
        return os.path.join(cache, f"{voice_id}.onnx")

    def _synth_bytes(self, text: str) -> bytes:
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(self.sample_rate)
            self._voice.synthesize(text, w)
        return buf.getvalue()

    async def synthesize(self, text: str) -> AudioChunk:
        data = await asyncio.to_thread(self._synth_bytes, text)
        return AudioChunk(data=data, sample_rate=self.sample_rate, is_final=True, text=text)

    async def stream_synthesize(
        self, text_stream: AsyncIterator[str]
    ) -> AsyncIterator[AudioChunk]:
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
