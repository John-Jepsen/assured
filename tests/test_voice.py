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


@pytest.mark.asyncio
async def test_barge_in_mid_statement_does_not_abandon_the_connection(global_db_seeded):
    """Barge-in that lands *inside* a database statement must not abandon the connection.

    Cancelling an await that is suspended inside SQLAlchemy's ``greenlet_spawn`` -- i.e.
    while a statement is in flight on the driver -- leaves the DBAPI connection in a state
    SQLAlchemy cannot reset. It treats the cancellation as a disconnect, invalidates the
    pool entry and builds a fresh one, so the pool *looks* healthy while the original
    connection survives detached with its transaction still open, released only when the
    garbage collector gets to it. On SQLite that surfaces as ``database is locked``; on
    PostgreSQL/asyncpg it is row locks held for an unbounded time.

    The window between "statement issued" and "connection reset" is far too short to hit
    by racing a real INSERT, so the statement is parked on a SQLite user function that
    blocks the aiosqlite worker thread. It is a real statement in flight on the session's
    real connection; only its duration is synthetic. The preceding UPDATE opens the write
    transaction whose fate the post-conditions assert on.

    The pool-invalidation signal is the deterministic one: SQLAlchemy discarding the
    connection *is* the abandonment. How quickly the abandoned connection then stops
    holding its locks is up to the driver and the garbage collector -- which is exactly
    why the connection must not be abandoned in the first place.
    """
    import threading

    from insurance_ai.api import routes_voice
    from insurance_ai.db.base import get_engine
    from insurance_ai.db.init import create_schema, drop_schema
    from insurance_ai.security.identity import demo_principal
    from sqlalchemy import event, text
    from sqlalchemy.exc import OperationalError

    engine = get_engine()
    # Start from an empty pool so every connection used below carries the park function
    # and a short busy timeout (a blocked statement should fail fast, not hang for 5s).
    await engine.dispose()

    entered = threading.Event()
    release = threading.Event()
    discarded: list[str] = []
    retained: list = []

    def _park() -> int:
        entered.set()
        release.wait(20)
        return 1

    @event.listens_for(engine.sync_engine, "connect")
    def _configure(dbapi_connection, _record):  # pragma: no cover - driver callback
        # Keeping a reference denies the garbage collector the chance to quietly tidy up
        # an abandoned connection and mask the leak.
        retained.append(dbapi_connection)
        dbapi_connection.create_function("test_park", 0, _park)
        dbapi_connection.execute("PRAGMA busy_timeout = 250")

    @event.listens_for(engine.sync_engine.pool, "invalidate")
    def _on_invalidate(*_args):  # pragma: no cover - only on the broken path
        discarded.append("invalidate")

    original_finish = routes_voice._service.finish_message

    async def _parked_finish(db, turn, answer):
        await db.execute(
            text("UPDATE conversations SET channel = channel WHERE id = :cid"),
            {"cid": turn.conversation.id},
        )
        await db.execute(text("SELECT test_park()"))
        return await original_finish(db, turn, answer)

    def _worker_threads() -> list[threading.Thread]:
        return [t for t in threading.enumerate() if "_connection_worker_thread" in t.name]

    baseline = len(_worker_threads())
    routes_voice._service.finish_message = _parked_finish
    try:
        ws = _FakeWS()
        task = asyncio.create_task(
            routes_voice._run_response(
                ws,
                None,
                b"",
                0.0,
                demo_principal(),
                pretranscribed="Does auto insurance cover a rental car?",
                request_id="rid-voice-cancel",
            )
        )
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, entered.wait, 20)
        assert entered.is_set(), "the parked statement never started"

        task.cancel()  # the user barged in while the write statement was in flight
        await asyncio.sleep(0.25)  # let the cancellation land on the parked statement
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        routes_voice._service.finish_message = original_finish
        release.set()
        event.remove(engine.sync_engine, "connect", _configure)
        event.remove(engine.sync_engine.pool, "invalidate", _on_invalidate)

    assert not discarded, (
        "the cancellation forced SQLAlchemy to invalidate the connection instead of "
        "resetting it: the statement was abandoned mid-flight"
    )
    # Barge-in semantics are unchanged: the interrupted turn still reports no 'done'.
    assert not any(m["type"] == "done" for m in ws.sent)

    # No abandoned connection is left holding SQLite's write lock.
    try:
        await drop_schema(engine)
    except OperationalError as exc:  # pragma: no cover - only on the broken path
        pytest.fail(f"an abandoned connection still holds the write lock: {exc}")
    await create_schema(engine)

    # Disposing the pool stops every connection the pool owns; an aiosqlite worker still
    # running afterwards belongs to a connection that was detached and leaked.
    await engine.dispose()
    for _ in range(50):
        if len(_worker_threads()) <= baseline:
            break
        await asyncio.sleep(0.02)
    assert len(_worker_threads()) <= baseline, "an aiosqlite connection worker leaked"
    retained.clear()
