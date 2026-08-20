"""Telephony abstraction. Core agent logic never depends on the provider.

Twilio Media Streams is the selected provider (see ADR). A provider yields inbound
audio frames and accepts outbound audio; the same orchestrator serves phone and web.
Without credentials the web + browser-voice experience is fully functional.
"""

from __future__ import annotations

from insurance_ai.config import get_settings


def twiml_media_stream(ws_url: str) -> str:
    """Return TwiML that greets the caller and opens a bidirectional media stream."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        "<Say>Thanks for calling the synthetic insurance demo. "
        "Please describe how I can help after the tone.</Say>"
        f'<Connect><Stream url="{ws_url}"/></Connect>'
        "</Response>"
    )


def telephony_status() -> dict:
    settings = get_settings()
    return {
        "provider": settings.telephony_provider,
        "enabled": settings.is_telephony_enabled,
        "public_base_url": settings.public_base_url,
        "note": (
            "Twilio configured — inbound calls supported."
            if settings.is_telephony_enabled
            else "Telephony not configured. Web + browser voice work fully without it."
        ),
    }
