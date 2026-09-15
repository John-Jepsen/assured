"""Durable, expiring, per-conversation security and transaction state."""

from __future__ import annotations

import asyncio
import json
import time
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from typing import Any

from insurance_ai.config import get_settings
from insurance_ai.domain.enums import VerificationStatus


@dataclass
class Session:
    conversation_id: str
    customer_id: str | None = None
    verification_status: VerificationStatus = VerificationStatus.UNVERIFIED
    attempts: int = 0
    satisfied_factors: list[str] = field(default_factory=list)
    escalated: bool = False
    pending_payment: dict[str, Any] | None = None
    pending_claim: dict[str, Any] | None = None

    @property
    def is_verified(self) -> bool:
        return self.verification_status == VerificationStatus.VERIFIED

    def to_json(self) -> str:
        payload = asdict(self)
        payload["verification_status"] = self.verification_status.value
        return json.dumps(payload, separators=(",", ":"), sort_keys=True)

    @classmethod
    def from_json(cls, payload: str | bytes) -> Session:
        values = json.loads(payload)
        values["verification_status"] = VerificationStatus(values["verification_status"])
        return cls(**values)


class SessionStore(ABC):
    """Store contract with an atomic per-conversation mutation boundary."""

    @abstractmethod
    async def get(self, conversation_id: str) -> Session: ...

    @abstractmethod
    async def save(self, session: Session) -> None: ...

    @abstractmethod
    async def reset(self, conversation_id: str) -> None: ...

    @abstractmethod
    async def ping(self) -> bool: ...

    async def aclose(self) -> None:
        return None

    @asynccontextmanager
    @abstractmethod
    async def locked(self, conversation_id: str) -> AsyncIterator[Session]:
        """Load, exclusively mutate, and persist one conversation session."""
        yield Session(conversation_id=conversation_id)  # pragma: no cover


class MemorySessionStore(SessionStore):
    """TTL-aware process-local implementation for tests and development."""

    def __init__(
        self,
        ttl_seconds: int,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.ttl_seconds = ttl_seconds
        self._clock = clock
        self._sessions: dict[str, tuple[Session, float]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def get(self, conversation_id: str) -> Session:
        record = self._sessions.get(conversation_id)
        if record and record[1] > self._clock():
            return record[0]
        self._sessions.pop(conversation_id, None)
        return Session(conversation_id=conversation_id)

    async def save(self, session: Session) -> None:
        self._sessions[session.conversation_id] = (
            session,
            self._clock() + self.ttl_seconds,
        )

    async def reset(self, conversation_id: str) -> None:
        self._sessions.pop(conversation_id, None)
        self._locks.pop(conversation_id, None)

    async def ping(self) -> bool:
        return True

    @asynccontextmanager
    async def locked(self, conversation_id: str) -> AsyncIterator[Session]:
        lock = self._locks.setdefault(conversation_id, asyncio.Lock())
        async with lock:
            session = await self.get(conversation_id)
            try:
                yield session
            finally:
                await self.save(session)


class RedisSessionStore(SessionStore):
    """Redis-backed TTL sessions with distributed per-conversation locks."""

    def __init__(
        self,
        url: str,
        ttl_seconds: int,
        *,
        lock_timeout_seconds: int = 180,
        lock_blocking_timeout_seconds: int = 10,
        client=None,
    ) -> None:
        if client is None:
            try:
                from redis.asyncio import Redis
            except ImportError as exc:  # pragma: no cover - production image includes it
                raise RuntimeError("session_backend=redis requires the redis package") from exc
            client = Redis.from_url(url, decode_responses=True)
        self._redis = client
        self.ttl_seconds = ttl_seconds
        self.lock_timeout_seconds = lock_timeout_seconds
        self.lock_blocking_timeout_seconds = lock_blocking_timeout_seconds

    @staticmethod
    def _key(conversation_id: str) -> str:
        return f"assured:session:{conversation_id}"

    @staticmethod
    def _lock_key(conversation_id: str) -> str:
        return f"assured:session-lock:{conversation_id}"

    async def get(self, conversation_id: str) -> Session:
        payload = await self._redis.get(self._key(conversation_id))
        return Session.from_json(payload) if payload else Session(conversation_id=conversation_id)

    async def save(self, session: Session) -> None:
        await self._redis.set(
            self._key(session.conversation_id),
            session.to_json(),
            ex=self.ttl_seconds,
        )

    async def reset(self, conversation_id: str) -> None:
        await self._redis.delete(self._key(conversation_id))

    async def ping(self) -> bool:
        return bool(await self._redis.ping())

    async def aclose(self) -> None:
        await self._redis.aclose()

    @asynccontextmanager
    async def locked(self, conversation_id: str) -> AsyncIterator[Session]:
        lock = self._redis.lock(
            self._lock_key(conversation_id),
            timeout=self.lock_timeout_seconds,
            blocking_timeout=self.lock_blocking_timeout_seconds,
        )
        async with lock:
            session = await self.get(conversation_id)
            try:
                yield session
            finally:
                await self.save(session)


@lru_cache
def get_session_store() -> SessionStore:
    settings = get_settings()
    if settings.session_backend == "redis":
        return RedisSessionStore(
            settings.redis_url or "",
            settings.session_ttl_seconds,
            lock_timeout_seconds=settings.session_lock_timeout_seconds,
            lock_blocking_timeout_seconds=settings.session_lock_blocking_timeout_seconds,
        )
    return MemorySessionStore(settings.session_ttl_seconds)
