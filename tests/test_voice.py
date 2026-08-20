"""Voice pipeline: STT->agent->TTS produces streamed audio + metrics; barge-in
cancels an in-flight spoken response so the user never waits it out."""

from __future__ import annotations

import asyncio

import pytest


class _FakeWS:
    """Records server->client frames; each send yields control so a mid-response
    cancellation (barge-in) can land between frames."""

    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_json(self, payload: dict) -> None:
        self.sent.append(payload)
        await asyncio.sleep(0.005)


@pytest.mark.asyncio
async def test_voice_produces_transcript_audio_and_metrics(global_db_seeded):
    from insurance_ai.api.routes_voice import _run_response

    ws = _FakeWS()
    await _run_response(ws, None, b"", 0.0, pretranscribed="Does auto insurance cover a rental car?")
    types = [m["type"] for m in ws.sent]
    assert "transcript" in types
    assert "audio" in types
    assert "metrics" in types
    assert types[-1] == "done"
    # audio frames carry real WAV bytes
    import base64

    audio = [m for m in ws.sent if m["type"] == "audio"][0]
    assert base64.b64decode(audio["data"])[:4] == b"RIFF"


@pytest.mark.asyncio
async def test_barge_in_cancels_in_flight_response(global_db_seeded):
    from insurance_ai.api.routes_voice import _run_response

    ws = _FakeWS()
    task = asyncio.create_task(
        _run_response(ws, None, b"", 0.0,
                      pretranscribed="Tell me everything about my claim and my billing and my policy.")
    )
    await asyncio.sleep(0.008)  # let it start streaming
    task.cancel()  # user barged in
    with pytest.raises(asyncio.CancelledError):
        await task
    # It was interrupted before completing — no terminal 'done' frame was sent.
    assert not any(m["type"] == "done" for m in ws.sent)
