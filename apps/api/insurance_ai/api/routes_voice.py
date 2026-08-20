"""Voice WebSocket: audio in -> STT -> orchestrator -> streaming TTS -> audio out.

Protocol (JSON control + base64 audio, so it works over a single JSON channel):
  client -> {"type":"audio","data":<b64 pcm/mock>} ...            (stream mic audio)
           {"type":"end","conversation_id":...}                   (utterance finished)
           {"type":"barge_in"}                                    (user interrupted TTS)
  server -> {"type":"partial","text":...}                         (interim transcript)
           {"type":"transcript","text":...}                       (final transcript)
           {"type":"token","token":...}                           (LLM token)
           {"type":"audio","data":<b64 wav>,"text":...}           (TTS chunk)
           {"type":"metrics", ...}                                (per-stage latency)
           {"type":"done","answer":...,"sources":...}
           {"type":"interrupted"}                                 (barge-in ack)

Barge-in: a JSON "barge_in" cancels the in-flight response task immediately, so the
user never waits for the assistant to finish speaking.
"""

from __future__ import annotations

import asyncio
import base64
import time
import uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from insurance_ai.api.service import ConversationService
from insurance_ai.agents.specialists import SPECIALISTS
from insurance_ai.db.base import SessionFactory
from insurance_ai.domain.enums import AgentName
from insurance_ai.observability import get_logger
from insurance_ai.providers.factory import get_providers

router = APIRouter(tags=["voice"])
log = get_logger("voice")
_service = ConversationService()


async def _run_response(
    ws: WebSocket, conversation_id: str | None, audio: bytes, t_end: float,
    pretranscribed: str | None = None,
):
    providers = get_providers()
    metrics: dict[str, float] = {}

    # STT — server-side (faster-whisper/mock) OR a client-provided transcript from the
    # browser Web Speech API (real client-side STT; the server still runs agent + TTS).
    if pretranscribed is not None:
        transcript = pretranscribed.strip()
    else:
        stt_result = await providers.stt.transcribe(audio)
        transcript = stt_result.text.strip()
    metrics["speech_end_to_transcript_ms"] = (time.perf_counter() - t_end) * 1000
    await ws.send_json({"type": "transcript", "text": transcript, "confidence": 1.0})
    if not transcript:
        await ws.send_json({"type": "done", "answer": "", "sources": []})
        return

    # Orchestrate
    async with SessionFactory() as db:
        result, _sess, conv = await _service.handle_message(
            db, conversation_id, transcript, channel="web", request_id=str(uuid.uuid4())
        )
    agent = SPECIALISTS.get(AgentName(result.trace.agents[0])) if result.trace.agents else None
    sys_prompt = agent.system_prompt if agent else ""

    # Stream LLM tokens -> feed incremental TTS
    first_token_at = {"t": None}
    first_audio_at = {"t": None}

    async def token_stream():
        async for token in _service.orchestrator.stream_answer(result.answer, transcript, sys_prompt):
            if first_token_at["t"] is None:
                first_token_at["t"] = time.perf_counter()
                metrics["transcript_to_first_token_ms"] = (
                    first_token_at["t"] - (t_end + metrics["speech_end_to_transcript_ms"] / 1000)
                ) * 1000
            await ws.send_json({"type": "token", "token": token})
            yield token

    async for chunk in providers.tts.stream_synthesize(token_stream()):
        if not chunk.data and chunk.is_final:
            continue
        if first_audio_at["t"] is None and chunk.data:
            first_audio_at["t"] = time.perf_counter()
            metrics["speech_end_to_first_audio_ms"] = (first_audio_at["t"] - t_end) * 1000
            if first_token_at["t"]:
                metrics["first_token_to_first_audio_ms"] = (
                    first_audio_at["t"] - first_token_at["t"]
                ) * 1000
        if chunk.data:
            await ws.send_json({
                "type": "audio", "data": base64.b64encode(chunk.data).decode(),
                "text": chunk.text, "sample_rate": chunk.sample_rate,
            })

    await ws.send_json({"type": "metrics", **{k: round(v, 1) for k, v in metrics.items()}})
    await ws.send_json({
        "type": "done", "answer": result.answer, "sources": result.trace.sources,
        "conversation_id": conv.id, "trace": result.trace.as_dict(),
    })


@router.websocket("/ws/voice")
async def ws_voice(ws: WebSocket) -> None:
    await ws.accept()
    buffer = bytearray()
    task: asyncio.Task | None = None
    conversation_id: str | None = None
    try:
        while True:
            msg = await ws.receive_json()
            mtype = msg.get("type")
            if mtype == "audio":
                buffer += base64.b64decode(msg.get("data", ""))
            elif mtype == "barge_in":
                if task and not task.done():
                    task.cancel()
                    await ws.send_json({"type": "interrupted"})
                buffer.clear()
            elif mtype in ("end", "utterance_text"):
                conversation_id = msg.get("conversation_id") or conversation_id
                if task and not task.done():
                    task.cancel()
                audio = bytes(buffer)
                buffer.clear()
                pretranscribed = msg.get("text") if mtype == "utterance_text" else None
                t_end = time.perf_counter()
                # Run the response as a background task so the receive loop stays free
                # to process a barge-in (interruption) while the assistant is speaking.
                task = asyncio.create_task(
                    _run_response(ws, conversation_id, audio, t_end, pretranscribed)
                )

                def _log_done(t: asyncio.Task) -> None:
                    if not t.cancelled() and t.exception():
                        log.error("voice_response_error", error=type(t.exception()).__name__)

                task.add_done_callback(_log_done)
            elif mtype == "close":
                break
    except WebSocketDisconnect:
        log.info("ws_voice_disconnect")
    except Exception as e:
        log.error("ws_voice_error", error=type(e).__name__)
    finally:
        if task and not task.done():
            task.cancel()
