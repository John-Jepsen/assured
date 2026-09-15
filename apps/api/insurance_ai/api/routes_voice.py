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
user never waits for the assistant to finish speaking. The cancellation cuts the token
and TTS stream short, but the turn's persistence runs under
:func:`insurance_ai.db.cancellation.uninterruptible` so it can never be abandoned with a
statement in flight -- see that module for what an abandoned connection costs.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import time

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from insurance_ai.api.schemas import MAX_CHAT_MESSAGE_CHARS, MAX_CONVERSATION_ID_CHARS
from insurance_ai.api.service import ConversationService
from insurance_ai.db.base import SessionFactory
from insurance_ai.db.cancellation import uninterruptible
from insurance_ai.observability import get_logger
from insurance_ai.observability.middleware import request_id_from_scope
from insurance_ai.providers.factory import get_providers
from insurance_ai.security.identity import Principal, websocket_principal
from insurance_ai.security.origin import websocket_origin_allowed
from insurance_ai.security.rate_limit import (
    websocket_action_allowed,
    websocket_ip_connection_allowed,
    websocket_principal_connection_allowed,
)

router = APIRouter(tags=["voice"])
log = get_logger("voice")
_service = ConversationService()
MAX_AUDIO_BUFFER_BYTES = 10 * 1024 * 1024


async def _run_response(
    ws: WebSocket,
    conversation_id: str | None,
    audio: bytes,
    t_end: float,
    principal: Principal,
    pretranscribed: str | None = None,
    *,
    request_id: str,
):
    """Run one utterance. ``request_id`` is the connection's correlation ID, minted or
    validated once by :class:`ObservabilityMiddleware`, so every turn of a voice call
    shares the ID an operator sees in the logs and in the trace."""
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

    # Orchestrate, stream, then persist exactly what the caller received.
    async with SessionFactory() as db:
        # Planning writes the user message and runs the state-changing tools; a
        # barge-in landing here must not tear a half-applied turn out of the database.
        turn = await uninterruptible(
            _service.prepare_message(
                db,
                conversation_id,
                transcript,
                principal=principal,
                channel="web",
                request_id=request_id,
            )
        )
        result = turn.result
        conv = turn.conversation

        # Stream LLM tokens -> feed incremental TTS
        first_token_at = {"t": None}
        first_audio_at = {"t": None}
        rendered: list[str] = []

        async def token_stream():
            async for token in _service.orchestrator.stream_result(result, transcript):
                rendered.append(token)
                if first_token_at["t"] is None:
                    first_token_at["t"] = time.perf_counter()
                    metrics["transcript_to_first_token_ms"] = (
                        first_token_at["t"]
                        - (t_end + metrics["speech_end_to_transcript_ms"] / 1000)
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
                await ws.send_json(
                    {
                        "type": "audio",
                        "data": base64.b64encode(chunk.data).decode(),
                        "text": chunk.text,
                        "sample_rate": chunk.sample_rate,
                    }
                )

        result, _sess, conv = await uninterruptible(
            _service.finish_message(db, turn, "".join(rendered))
        )

    await ws.send_json({"type": "metrics", **{k: round(v, 1) for k, v in metrics.items()}})
    await ws.send_json(
        {
            "type": "done",
            "answer": result.answer,
            "sources": result.trace.sources,
            "conversation_id": conv.id,
            "trace": result.trace.as_dict(),
        }
    )


@router.websocket("/ws/voice")
async def ws_voice(ws: WebSocket) -> None:
    if not websocket_origin_allowed(ws.headers.get("origin")):
        await ws.close(code=1008, reason="Origin not allowed")
        return
    if not await websocket_ip_connection_allowed(ws):
        await ws.close(code=1013, reason="Rate limit exceeded")
        return
    try:
        principal = await websocket_principal(ws)
    except HTTPException:
        await ws.close(code=1008, reason="Authentication required")
        return
    if not await websocket_principal_connection_allowed(principal):
        await ws.close(code=1013, reason="Rate limit exceeded")
        return
    await ws.accept()
    request_id = request_id_from_scope(ws.scope)
    buffer = bytearray()
    task: asyncio.Task | None = None
    conversation_id: str | None = None
    try:
        while True:
            msg = await ws.receive_json()
            if principal.is_expired():
                await ws.close(code=1008, reason="Access token expired")
                break
            mtype = msg.get("type")
            if mtype == "audio":
                try:
                    chunk = base64.b64decode(msg.get("data", ""), validate=True)
                except (binascii.Error, TypeError, ValueError):
                    await ws.send_json(
                        {
                            "type": "error",
                            "code": "invalid_audio",
                            "message": "The audio frame is not valid base64.",
                        }
                    )
                    continue
                if len(buffer) + len(chunk) > MAX_AUDIO_BUFFER_BYTES:
                    await ws.close(code=1009, reason="Audio buffer too large")
                    break
                buffer += chunk
            elif mtype == "barge_in":
                if task and not task.done():
                    task.cancel()
                    await ws.send_json({"type": "interrupted"})
                buffer.clear()
            elif mtype in ("end", "utterance_text"):
                if not await websocket_action_allowed(ws, principal):
                    await ws.close(code=1013, reason="Rate limit exceeded")
                    break
                supplied_conversation_id = msg.get("conversation_id")
                if supplied_conversation_id is not None and (
                    not isinstance(supplied_conversation_id, str)
                    or not 1 <= len(supplied_conversation_id) <= MAX_CONVERSATION_ID_CHARS
                ):
                    await ws.send_json(
                        {
                            "type": "error",
                            "code": "invalid_request",
                            "message": "The conversation ID is malformed.",
                        }
                    )
                    continue
                conversation_id = supplied_conversation_id or conversation_id
                if task and not task.done():
                    task.cancel()
                audio = bytes(buffer)
                buffer.clear()
                pretranscribed = msg.get("text") if mtype == "utterance_text" else None
                if pretranscribed is not None and (
                    not isinstance(pretranscribed, str)
                    or not 1 <= len(pretranscribed.strip()) <= MAX_CHAT_MESSAGE_CHARS
                ):
                    await ws.send_json(
                        {
                            "type": "error",
                            "code": "invalid_request",
                            "message": "The transcript is empty, too large, or malformed.",
                        }
                    )
                    continue
                t_end = time.perf_counter()
                # Run the response as a background task so the receive loop stays free
                # to process a barge-in (interruption) while the assistant is speaking.
                task = asyncio.create_task(
                    _run_response(
                        ws,
                        conversation_id,
                        audio,
                        t_end,
                        principal,
                        pretranscribed,
                        request_id=request_id,
                    )
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
