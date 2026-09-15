"""Distributed, privacy-preserving request rate limits.

Production counters live in Redis so limits are consistent across API replicas.
The in-memory implementation is intentionally bounded and exists only for the
local demo and tests.
"""

from __future__ import annotations

import asyncio
import hashlib
import math
import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Protocol

from fastapi import Depends, HTTPException, Request, WebSocket, status

from insurance_ai.config import Settings, get_settings
from insurance_ai.observability import get_logger
from insurance_ai.security.identity import (
    Principal,
    admin_principal,
    customer_principal,
)

log = get_logger("rate_limit")

_INCREMENT_SCRIPT = """
local count = redis.call('INCR', KEYS[1])
if count == 1 then
  redis.call('EXPIRE', KEYS[1], ARGV[1])
end
local ttl = redis.call('TTL', KEYS[1])
return {count, ttl}
"""


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    retry_after: int
    remaining: int


class RateLimiter(Protocol):
    async def hit(
        self,
        namespace: str,
        identifier: str,
        *,
        limit: int,
        window_seconds: int,
    ) -> RateLimitDecision: ...

    async def ping(self) -> bool: ...

    async def aclose(self) -> None: ...


def _counter_key(namespace: str, identifier: str) -> str:
    # Subjects and network addresses are security-sensitive identifiers. Hashing
    # keeps them out of Redis key listings and infrastructure diagnostics.
    digest = hashlib.sha256(identifier.encode("utf-8")).hexdigest()
    return f"assured:rate:{namespace}:{digest}"


class MemoryRateLimiter:
    """Process-local fixed-window limiter with a hard cardinality bound."""

    def __init__(
        self,
        max_buckets: int,
        *,
        clock=time.monotonic,
    ) -> None:
        self.max_buckets = max_buckets
        self._clock = clock
        self._buckets: dict[str, tuple[int, float]] = {}
        self._lock = asyncio.Lock()

    async def hit(
        self,
        namespace: str,
        identifier: str,
        *,
        limit: int,
        window_seconds: int,
    ) -> RateLimitDecision:
        now = self._clock()
        key = _counter_key(namespace, identifier)
        async with self._lock:
            record = self._buckets.get(key)
            if record is None or record[1] <= now:
                if record is None and len(self._buckets) >= self.max_buckets:
                    self._buckets = {
                        candidate: bucket
                        for candidate, bucket in self._buckets.items()
                        if bucket[1] > now
                    }
                if key not in self._buckets and len(self._buckets) >= self.max_buckets:
                    return RateLimitDecision(False, window_seconds, 0)
                count, expires_at = 0, now + window_seconds
            else:
                count, expires_at = record
            count += 1
            self._buckets[key] = (count, expires_at)
        return RateLimitDecision(
            allowed=count <= limit,
            retry_after=max(1, math.ceil(expires_at - now)),
            remaining=max(0, limit - count),
        )

    async def ping(self) -> bool:
        return True

    async def aclose(self) -> None:
        return None


class RedisRateLimiter:
    """Atomic fixed-window counters shared by every API replica."""

    def __init__(self, url: str, *, client=None) -> None:
        if client is None:
            try:
                from redis.asyncio import Redis
            except ImportError as exc:  # pragma: no cover - production image includes it
                raise RuntimeError("distributed rate limits require the redis package") from exc
            client = Redis.from_url(url, decode_responses=True)
        self._redis = client

    async def hit(
        self,
        namespace: str,
        identifier: str,
        *,
        limit: int,
        window_seconds: int,
    ) -> RateLimitDecision:
        count, ttl = await self._redis.eval(
            _INCREMENT_SCRIPT,
            1,
            _counter_key(namespace, identifier),
            window_seconds,
        )
        count = int(count)
        ttl = int(ttl)
        return RateLimitDecision(
            allowed=count <= limit,
            retry_after=max(1, ttl if ttl >= 0 else window_seconds),
            remaining=max(0, limit - count),
        )

    async def ping(self) -> bool:
        return bool(await self._redis.ping())

    async def aclose(self) -> None:
        await self._redis.aclose()


@lru_cache
def get_rate_limiter() -> RateLimiter:
    settings = get_settings()
    if settings.session_backend == "redis":
        return RedisRateLimiter(settings.redis_url or "")
    return MemoryRateLimiter(settings.rate_limit_memory_max_buckets)


async def check_limit(
    namespace: str,
    identifier: str,
    *,
    limit: int,
    settings: Settings | None = None,
) -> RateLimitDecision:
    """Apply a limit and honor the explicitly configured failure policy."""
    settings = settings or get_settings()
    if not settings.rate_limit_enabled:
        return RateLimitDecision(True, 0, limit)
    try:
        return await get_rate_limiter().hit(
            namespace,
            identifier,
            limit=limit,
            window_seconds=settings.rate_limit_window_seconds,
        )
    except Exception as exc:
        log.error("rate_limit_backend_error", namespace=namespace, error=type(exc).__name__)
        return RateLimitDecision(
            allowed=settings.rate_limit_fail_open,
            retry_after=settings.rate_limit_window_seconds,
            remaining=0,
        )


def client_ip(connection: Request | WebSocket) -> str:
    """Use the ASGI server's trusted client address, never a spoofable raw header."""
    return connection.client.host if connection.client else "unknown"


async def http_ip_limit(request: Request) -> RateLimitDecision:
    settings = get_settings()
    return await check_limit(
        "http-ip",
        client_ip(request),
        limit=settings.rate_limit_http_ip_requests,
        settings=settings,
    )


def _rate_limit_error(decision: RateLimitDecision) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail="Too many requests",
        headers={"Retry-After": str(decision.retry_after)},
    )


async def _limit_principal(principal: Principal) -> Principal:
    settings = get_settings()
    decision = await check_limit(
        "http-principal",
        principal.subject,
        limit=settings.rate_limit_http_principal_requests,
        settings=settings,
    )
    if not decision.allowed:
        raise _rate_limit_error(decision)
    return principal


async def rate_limited_customer_principal(
    principal: Principal = Depends(customer_principal),
) -> Principal:
    return await _limit_principal(principal)


async def rate_limited_admin_principal(
    principal: Principal = Depends(admin_principal),
) -> Principal:
    return await _limit_principal(principal)


async def websocket_ip_connection_allowed(websocket: WebSocket) -> bool:
    settings = get_settings()
    decision = await check_limit(
        "ws-connect-ip",
        client_ip(websocket),
        limit=settings.rate_limit_websocket_ip_connections,
        settings=settings,
    )
    return decision.allowed


async def websocket_principal_connection_allowed(principal: Principal) -> bool:
    settings = get_settings()
    decision = await check_limit(
        "ws-connect-principal",
        principal.subject,
        limit=settings.rate_limit_websocket_principal_connections,
        settings=settings,
    )
    return decision.allowed


async def websocket_action_allowed(websocket: WebSocket, principal: Principal) -> bool:
    """Limit expensive chat turns or completed voice utterances on both dimensions."""
    settings = get_settings()
    ip_decision = await check_limit(
        "ws-action-ip",
        client_ip(websocket),
        limit=settings.rate_limit_websocket_ip_actions,
        settings=settings,
    )
    if not ip_decision.allowed:
        return False
    principal_decision = await check_limit(
        "ws-action-principal",
        principal.subject,
        limit=settings.rate_limit_websocket_principal_actions,
        settings=settings,
    )
    return principal_decision.allowed
