"""Telephony routes: Twilio voice webhook + media-stream bridge (gated)."""

from __future__ import annotations

import base64
import json
import uuid

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import PlainTextResponse

from insurance_ai.api.service import ConversationService
from insurance_ai.config import get_settings
from insurance_ai.db.base import SessionFactory
from insurance_ai.observability import get_logger
from insurance_ai.providers.factory import get_providers
from insurance_ai.telephony import telephony_status, twiml_media_stream

router = APIRouter(prefix="/api/telephony", tags=["telephony"])
log = get_logger("telephony")
_service = ConversationService()


@router.get("/status")
async def status() -> dict:
    return telephony_status()


@router.post("/voice")
async def voice_webhook(request: Request) -> PlainTextResponse:
    """Twilio inbound-call webhook → TwiML that opens a media stream to us."""
    settings = get_settings()
    if not settings.is_telephony_enabled:
        return PlainTextResponse(
            '<?xml version="1.0" encoding="UTF-8"?><Response>'
            "<Say>The phone demo is not configured. Please use the web app.</Say>"
            "</Response>",
            media_type="application/xml",
        )
    base = (
        (settings.public_base_url or "").replace("https://", "wss://").replace("http://", "ws://")
    )
    ws_url = f"{base}/api/telephony/media"
    return PlainTextResponse(twiml_media_stream(ws_url), media_type="application/xml")


@router.websocket("/media")
async def media_stream(ws: WebSocket) -> None:
    """Twilio Media Stream bridge → same orchestrator as web/voice.

    Twilio sends base64 mulaw frames. Real STT (faster-whisper) transcribes them;
    utterance end is detected by a short silence gap. This is the same pipeline the
    browser voice path uses, proving telephony reuses the core agent system.
    """
    await ws.accept()
    providers = get_providers()
    buffer = bytearray()
    conversation_id: str | None = None
    try:
        while True:
            raw = await ws.receive_text()
            event = json.loads(raw)
            ev = event.get("event")
            if ev == "start":
                conversation_id = None
                log.info("call_start", call=event.get("start", {}).get("callSid"))
            elif ev == "media":
                buffer += base64.b64decode(event["media"]["payload"])
            elif ev == "mark" and event.get("mark", {}).get("name") == "utterance_end":
                audio = bytes(buffer)
                buffer.clear()
                stt = await providers.stt.transcribe(audio, sample_rate=8000)
                if stt.text.strip():
                    async with SessionFactory() as db:
                        result, _sess, conv = await _service.handle_message(
                            db,
                            conversation_id,
                            stt.text,
                            channel="phone",
                            request_id=str(uuid.uuid4()),
                        )
                        conversation_id = conv.id
                    audio_out = await providers.tts.synthesize(result.answer)
                    await ws.send_text(
                        json.dumps(
                            {
                                "event": "media",
                                "media": {"payload": base64.b64encode(audio_out.data).decode()},
                            }
                        )
                    )
            elif ev == "stop":
                break
    except WebSocketDisconnect:
        log.info("call_disconnect")
    except Exception as e:
        log.error("media_stream_error", error=type(e).__name__)
