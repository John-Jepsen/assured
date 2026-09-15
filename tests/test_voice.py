"""Voice pipeline: STT->agent->TTS produces streamed audio + metrics; barge-in
cancels an in-flight spoken response so the user never waits it out."""

from __future__ import annotations

import asyncio
import base64

import pytest


class _FakeWS:
    """Records server->client frames; each send yields control so a mid-response
    cancellation (barge-in) can land between frames.

    ``signal_on`` names a frame type to announce through :attr:`reached`, which lets a
    test cancel at a defined point in the response instead of racing a wall-clock
    sleep. That matters for more than determinism: ``_run_response`` streams its audio
    while a database session is open, and cancelling a task *mid-statement* abandons
    the driver connection (SQLAlchemy cannot reset a connection whose in-flight
    operation was cancelled), which leaves a SQLite write lock behind until the
    orphaned connection is garbage collected. Cancelling on a frame boundary keeps the
    interruption off the database and still exercises the real barge-in path.
    """

    def __init__(self, signal_on: str | None = None) -> None:
        self.sent: list[dict] = []
        self._signal_on = signal_on
        self.reached = asyncio.Event()

    async def send_json(self, payload: dict) -> None:
        self.sent.append(payload)
        if payload.get("type") == self._signal_on:
            self.reached.set()
        await asyncio.sleep(0.005)


@pytest.mark.asyncio
async def test_voice_produces_transcript_audio_and_metrics(global_db_seeded):
    from insurance_ai.api.routes_voice import _run_response
    from insurance_ai.security.identity import demo_principal

    ws = _FakeWS()
    await _run_response(
        ws,
        None,
        b"",
        0.0,
        demo_principal(),
        pretranscribed="Does auto insurance cover a rental car?",
        request_id="rid-voice",
    )
    types = [m["type"] for m in ws.sent]
    assert "transcript" in types
    assert "audio" in types
    assert "metrics" in types
    assert types[-1] == "done"
    # audio frames carry real WAV bytes
    audio = next(m for m in ws.sent if m["type"] == "audio")
    assert base64.b64decode(audio["data"])[:4] == b"RIFF"


@pytest.mark.asyncio
async def test_barge_in_cancels_in_flight_response(global_db_seeded):
    from insurance_ai.api.routes_voice import _run_response
    from insurance_ai.security.identity import demo_principal

    # Cancel on the first audio frame: the assistant is demonstrably speaking, which is
    # the only moment a user can actually barge in on.
    ws = _FakeWS(signal_on="audio")
    task = asyncio.create_task(
        _run_response(
            ws,
            None,
            b"",
            0.0,
            demo_principal(),
            pretranscribed=(
                "Tell me everything about my claim and my billing and my policy."
            ),
            request_id="rid-voice",
        )
    )
    await asyncio.wait_for(ws.reached.wait(), timeout=10)
    task.cancel()  # user barged in
    with pytest.raises(asyncio.CancelledError):
        await task
    # It was interrupted mid-speech, before completing — no terminal 'done' frame.
    assert any(m["type"] == "audio" for m in ws.sent)
    assert not any(m["type"] == "done" for m in ws.sent)


def test_voice_socket_reuses_middleware_request_id(api_client):
    """The voice path must correlate on the connection's x-request-id, not a private
    UUID, so an operator can follow a voice turn through the logs from one ID."""
    from insurance_ai.providers.mock import MockSTT

    audio = MockSTT.encode("Does auto insurance cover a rental car")
    supplied = "voice-corr-0001"
    with api_client.websocket_connect(
        "/ws/voice", headers={"x-request-id": supplied}
    ) as ws:
        ws.send_json({"type": "audio", "data": base64.b64encode(audio).decode()})
        ws.send_json({"type": "end"})
        while True:
            msg = ws.receive_json()
            if msg["type"] == "done":
                assert msg["trace"]["request_id"] == supplied
                break


def test_voice_turns_share_one_connection_request_id(api_client):
    """Two utterances on one socket carry the same correlation ID (turn identity stays
    with the persisted message, not the request ID)."""
    from insurance_ai.providers.mock import MockSTT

    audio = MockSTT.encode("Does auto insurance cover a rental car")
    seen: list[str] = []
    with api_client.websocket_connect("/ws/voice") as ws:
        for _ in range(2):
            ws.send_json({"type": "audio", "data": base64.b64encode(audio).decode()})
            ws.send_json({"type": "end"})
            while True:
                msg = ws.receive_json()
                if msg["type"] == "done":
                    seen.append(msg["trace"]["request_id"])
                    break
    assert seen[0] == seen[1]
    assert seen[0] != "unknown"
