"""Run a database statement to completion even if the surrounding task is cancelled.

Why this exists
---------------
Cancelling an ``await`` that is suspended *inside* SQLAlchemy's ``greenlet_spawn``
(i.e. while a statement is in flight on the driver) leaves the DBAPI connection in a
state SQLAlchemy cannot reset: the greenlet is unwound with ``CancelledError``, the
connection is flagged as disconnected and dropped from the pool, and a fresh one is
built in its place -- so the pool *looks* healthy. The original connection survives
detached, still holding whatever transaction was open, and is only released when the
garbage collector gets to it. On SQLite that surfaces as ``database is locked``; on
PostgreSQL/asyncpg it means row locks held for an unbounded time.

The fix is to make the window between "statement issued" and "connection reset"
uninterruptible. ``uninterruptible()`` defers the cancellation until the statement has
finished, then re-raises it, so the caller still unwinds promptly.

Why not a bare ``asyncio.shield``
---------------------------------
``await asyncio.shield(coro)`` is actively worse than doing nothing here: when the
outer await is cancelled the shielded coroutine keeps running, but the caller unwinds
immediately -- closing the ``async with SessionFactory() as db:`` block, and the
session, out from under the statement that is still using it. The awaited future must
be drained *before* the ``CancelledError`` is allowed to propagate, which is what the
loop below does.

Scope: wrap individual statements or short statement groups only. Wrapping a long
stream (e.g. a TTS/token loop) would make cancellation -- voice barge-in -- unresponsive.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any, TypeVar

T = TypeVar("T")

__all__ = ["uninterruptible"]


async def uninterruptible(coro: Coroutine[Any, Any, T]) -> T:
    """Await ``coro`` so that cancellation cannot abandon it mid-flight.

    If the calling task is cancelled while ``coro`` is running, ``coro`` is still run to
    completion (and its connection therefore reset) before the ``CancelledError`` is
    re-raised. Repeated cancellations -- barge-in followed by a disconnect, say -- are
    absorbed the same way rather than tearing the statement down on the second attempt.

    Exceptions raised by ``coro`` propagate unchanged.
    """
    fut = asyncio.ensure_future(coro)
    deferred: asyncio.CancelledError | None = None
    while True:
        try:
            result = await asyncio.shield(fut)
        except asyncio.CancelledError as exc:
            if fut.done():
                # The statement itself was cancelled or finished cancelled: nothing is
                # left in flight, so there is nothing to protect.
                raise
            # The *caller* was cancelled. Keep waiting; the session must stay open until
            # the statement completes and its connection is reset.
            deferred = exc
            continue
        break
    if deferred is not None:
        raise deferred
    return result
