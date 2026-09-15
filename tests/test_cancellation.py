"""Unit tests for the uninterruptible-await helper.

The integration proof -- that a barge-in cannot abandon a database connection
mid-statement -- lives in ``test_voice.py``. These cover the helper's own contract.
"""

from __future__ import annotations

import asyncio

import pytest

from insurance_ai.db.cancellation import uninterruptible


@pytest.mark.asyncio
async def test_a_cancelled_caller_still_runs_the_statement_to_completion():
    finished = asyncio.Event()

    async def statement() -> str:
        await asyncio.sleep(0.05)
        finished.set()
        return "committed"

    task = asyncio.create_task(uninterruptible(statement()))
    await asyncio.sleep(0)  # let the statement start
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert finished.is_set(), "the statement was abandoned instead of run to completion"


@pytest.mark.asyncio
async def test_repeated_cancellation_does_not_tear_the_statement_down():
    """Barge-in followed by a disconnect must be absorbed like the first cancellation."""
    finished = asyncio.Event()

    async def statement() -> None:
        await asyncio.sleep(0.05)
        finished.set()

    task = asyncio.create_task(uninterruptible(statement()))
    await asyncio.sleep(0)
    for _ in range(3):
        task.cancel()
        await asyncio.sleep(0.005)
    with pytest.raises(asyncio.CancelledError):
        await task
    assert finished.is_set()


@pytest.mark.asyncio
async def test_an_uncancelled_call_returns_its_result():
    async def statement() -> int:
        return 7

    assert await uninterruptible(statement()) == 7


@pytest.mark.asyncio
async def test_the_statements_own_exception_propagates_unchanged():
    class Failed(Exception):
        pass

    async def statement() -> None:
        raise Failed("rollback")

    with pytest.raises(Failed, match="rollback"):
        await uninterruptible(statement())
