"""Durable session serialization, expiry, and mutation-lock behavior."""

from __future__ import annotations

import asyncio
import os

import pytest
from insurance_ai.domain.enums import VerificationStatus
from insurance_ai.security.session import MemorySessionStore, RedisSessionStore, Session


class _FakeLock:
    def __init__(self, lock: asyncio.Lock) -> None:
        self._lock = lock

    async def __aenter__(self):
        await self._lock.acquire()
        return self

    async def __aexit__(self, *_args):
        self._lock.release()


class _FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.expiries: dict[str, int] = {}
        self.locks: dict[str, asyncio.Lock] = {}
        self.closed = False

    async def get(self, key: str):
        return self.values.get(key)

    async def set(self, key: str, value: str, *, ex: int):
        self.values[key] = value
        self.expiries[key] = ex

    async def delete(self, key: str):
        self.values.pop(key, None)

    async def ping(self):
        return True

    async def aclose(self):
        self.closed = True

    def lock(self, name: str, **_kwargs):
        return _FakeLock(self.locks.setdefault(name, asyncio.Lock()))


def test_session_round_trip_preserves_security_and_pending_state():
    original = Session(
        conversation_id="conv-1",
        customer_id="customer-1",
        verification_status=VerificationStatus.VERIFIED,
        attempts=2,
        satisfied_factors=["policy_number", "zip_code"],
        escalated=True,
        pending_payment={"invoice_number": "INV-1", "amount": 42.5},
        pending_claim={"policy_number": "AUTO-1", "loss_type": "collision"},
    )

    assert Session.from_json(original.to_json()) == original


@pytest.mark.asyncio
async def test_memory_sessions_expire_after_configured_ttl():
    now = {"value": 100.0}
    store = MemorySessionStore(30, clock=lambda: now["value"])
    session = Session("conv-1", customer_id="customer-1")
    await store.save(session)

    assert (await store.get("conv-1")).customer_id == "customer-1"
    now["value"] = 131.0
    assert (await store.get("conv-1")).customer_id is None


@pytest.mark.asyncio
async def test_memory_lock_serializes_concurrent_mutations():
    store = MemorySessionStore(60)

    async def increment():
        async with store.locked("conv-1") as session:
            previous = session.attempts
            await asyncio.sleep(0)
            session.attempts = previous + 1

    await asyncio.gather(*(increment() for _ in range(20)))
    assert (await store.get("conv-1")).attempts == 20


@pytest.mark.asyncio
async def test_redis_sessions_are_visible_across_store_instances_with_ttl():
    client = _FakeRedis()
    first = RedisSessionStore("redis://unused", 90, client=client)
    second = RedisSessionStore("redis://unused", 90, client=client)

    async with first.locked("conv-1") as session:
        session.customer_id = "customer-1"
        session.verification_status = VerificationStatus.VERIFIED

    restored = await second.get("conv-1")
    assert restored.customer_id == "customer-1"
    assert restored.is_verified
    assert client.expiries["assured:session:conv-1"] == 90
    assert await second.ping()

    await second.reset("conv-1")
    assert (await first.get("conv-1")).customer_id is None
    await first.aclose()
    assert client.closed


@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.environ.get("INSURANCE_AI_TEST_REDIS_URL"),
    reason="set INSURANCE_AI_TEST_REDIS_URL to exercise a real Redis server",
)
async def test_real_redis_lock_and_cross_instance_continuity():
    url = os.environ["INSURANCE_AI_TEST_REDIS_URL"]
    first = RedisSessionStore(url, 60)
    second = RedisSessionStore(url, 60)
    conversation_id = "integration-session"
    await first.reset(conversation_id)

    async def increment(store):
        async with store.locked(conversation_id) as session:
            previous = session.attempts
            await asyncio.sleep(0.01)
            session.attempts = previous + 1

    try:
        await asyncio.gather(*(increment(first if i % 2 else second) for i in range(10)))
        assert (await first.get(conversation_id)).attempts == 10
    finally:
        await first.reset(conversation_id)
        await first.aclose()
        await second.aclose()
