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
from ipaddress import IPv4Network, IPv6Network, ip_address, ip_network
from typing import Protocol

from fastapi import Depends, HTTPException, Request, WebSocket, status
from fastapi.responses import JSONResponse

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


@lru_cache(maxsize=8)
def _trusted_networks(proxies: tuple[str, ...]) -> tuple[IPv4Network | IPv6Network, ...]:
    """Parse the configured proxy allowlist once per distinct configuration."""
    # Settings validation already rejected anything that is not an address/network.
    return tuple(ip_network(entry, strict=False) for entry in proxies)


def _normalized_ip(raw: str) -> str | None:
    """Canonicalize one address so equivalent spellings share a counter, or reject it."""
    value = raw.strip()
    if value.startswith("["):  # [2001:db8::1]:443
        value = value[1:].partition("]")[0]
    elif value.count(":") == 1:  # 203.0.113.7:4443
        value = value.partition(":")[0]
    try:
        return str(ip_address(value))
    except ValueError:
        return None


def _is_trusted(address: str, trusted: tuple[IPv4Network | IPv6Network, ...]) -> bool:
    try:
        parsed = ip_address(address)
    except ValueError:
        return False
    return any(parsed in network for network in trusted)


def _forwarded_client(
    connection: Request | WebSocket,
    trusted: tuple[IPv4Network | IPv6Network, ...],
) -> str | None:
    """Resolve the caller the trusted proxy chain vouched for, or ``None``.

    A proxy appends the address it accepted the connection from, so the chain is
    only credible from the right. Trusted hops are skipped; the first untrusted
    address is the caller. Anything a client prepended stays to the left of that
    address and is therefore never reachable, which is what keeps a forged header
    from selecting someone else's bucket.
    """
    forwarded = connection.headers.get("x-forwarded-for")
    for hop in reversed(forwarded.split(",")) if forwarded else ():
        address = _normalized_ip(hop)
        if address is None:
            break  # A malformed chain is not evidence of anything; stop believing it.
        if not _is_trusted(address, trusted):
            return address
    # Single-hop proxies commonly send only X-Real-IP, which the proxy writes itself.
    return _normalized_ip(connection.headers.get("x-real-ip") or "")


def client_ip(connection: Request | WebSocket, settings: Settings | None = None) -> str:
    """Identify the caller, believing forwarded headers only from a trusted proxy.

    Without ``rate_limit_trusted_proxies`` the ASGI peer address is the only identity
    considered: forwarded headers are client-controlled, so honoring them from an
    arbitrary peer would let every caller pick its own bucket and escape the limit.
    """
    peer = connection.client.host if connection.client else None
    if peer is None:
        return "unknown"
    trusted = _trusted_networks(tuple((settings or get_settings()).rate_limit_trusted_proxies))
    if not trusted or not _is_trusted(peer, trusted):
        return peer
    return _forwarded_client(connection, trusted) or peer


async def http_ip_limit(request: Request) -> RateLimitDecision:
    settings = get_settings()
    return await check_limit(
        "http-ip",
        client_ip(request, settings),
        limit=settings.rate_limit_http_ip_requests,
        settings=settings,
    )


RATE_LIMITED_ERROR = "rate_limited"
RATE_LIMITED_MESSAGE = "Too many requests."


class RateLimitExceeded(HTTPException):
    """429 raised by the per-principal gate.

    ``rate_limit_exception_handler`` renders it as the same
    ``{"error": "rate_limited", "message": ...}`` body the per-IP HTTP middleware
    returns, so a client detects either limit with one check. The application
    factory must register the pair::

        app.add_exception_handler(RateLimitExceeded, rate_limit_exception_handler)

    Without that registration FastAPI falls back to its generic ``{"detail": ...}``
    rendering for this exception.
    """

    def __init__(self, decision: RateLimitDecision) -> None:
        super().__init__(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=RATE_LIMITED_MESSAGE,
            headers={"Retry-After": str(decision.retry_after)},
        )


async def rate_limit_exception_handler(
    _request: Request,
    exc: RateLimitExceeded,
) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": RATE_LIMITED_ERROR, "message": exc.detail},
        headers=exc.headers,
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
        raise RateLimitExceeded(decision)
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
        client_ip(websocket, settings),
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
        client_ip(websocket, settings),
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
