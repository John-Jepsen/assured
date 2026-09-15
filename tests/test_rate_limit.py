"""Distributed rate limits: window arithmetic, failure policy, and HTTP/WS gates."""

from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from insurance_ai.config import Settings
from insurance_ai.security import rate_limit
from insurance_ai.security.identity import Principal
from insurance_ai.security.rate_limit import (
    MemoryRateLimiter,
    RateLimitDecision,
    RedisRateLimiter,
    check_limit,
    client_ip,
    rate_limited_admin_principal,
    rate_limited_customer_principal,
    websocket_action_allowed,
    websocket_ip_connection_allowed,
    websocket_principal_connection_allowed,
)
from pydantic import ValidationError
from starlette.websockets import WebSocketDisconnect
from test_production_config import _production_settings

WINDOW = 60


class _CountingLimiter:
    """Records every call so tests can prove the limiter was (not) consulted."""

    def __init__(self) -> None:
        self.calls = 0

    async def hit(self, namespace, identifier, *, limit, window_seconds):
        self.calls += 1
        return RateLimitDecision(True, 0, limit)

    async def ping(self) -> bool:
        return True

    async def aclose(self) -> None:
        return None


class _BrokenLimiter:
    """Stands in for an unreachable Redis."""

    async def hit(self, namespace, identifier, *, limit, window_seconds):
        raise RuntimeError("rate-limit backend unavailable")

    async def ping(self) -> bool:
        return False

    async def aclose(self) -> None:
        return None


class _FakeRedis:
    """Minimal EVAL-compatible stand-in for the fixed-window Lua counter."""

    def __init__(self, *, ttl: int = 42) -> None:
        self.counts: dict[str, int] = {}
        self.ttl = ttl
        self.closed = False

    async def eval(self, _script: str, _numkeys: int, key: str, _window: int):
        self.counts[key] = self.counts.get(key, 0) + 1
        return [self.counts[key], self.ttl]

    async def ping(self) -> bool:
        return True

    async def aclose(self) -> None:
        self.closed = True


def _connection(host: str | None = "203.0.113.9"):
    """An object shaped like the Request/WebSocket attributes ``client_ip`` reads."""
    return SimpleNamespace(client=SimpleNamespace(host=host) if host else None)


@pytest.fixture
def limited(monkeypatch):
    """Install overridden settings plus a private limiter for the module under test.

    ``get_settings`` and ``get_rate_limiter`` are both ``lru_cache``d singletons, so
    tests patch the names inside ``security.rate_limit`` instead of mutating the real
    caches. ``monkeypatch`` restores them, and the counters live on a throwaway
    limiter, so nothing leaks into the rest of the suite.
    """

    def _install(**overrides) -> MemoryRateLimiter:
        settings = Settings(_env_file=None, **overrides)
        limiter = MemoryRateLimiter(settings.rate_limit_memory_max_buckets)
        monkeypatch.setattr(rate_limit, "get_settings", lambda: settings)
        monkeypatch.setattr(rate_limit, "get_rate_limiter", lambda: limiter)
        return limiter

    return _install


# --- counter keys and client identity ---------------------------------------


def test_counter_keys_namespace_and_hash_the_identifier():
    key = rate_limit._counter_key("http-ip", "203.0.113.7")

    assert key.startswith("assured:rate:http-ip:")
    assert "203.0.113.7" not in key
    assert key.endswith(hashlib.sha256(b"203.0.113.7").hexdigest())
    assert key != rate_limit._counter_key("http-principal", "203.0.113.7")


def test_client_ip_uses_the_asgi_peer_and_degrades_to_unknown():
    assert client_ip(_connection("198.51.100.4")) == "198.51.100.4"
    assert client_ip(_connection(None)) == "unknown"


# --- in-memory fixed window --------------------------------------------------


@pytest.mark.asyncio
async def test_memory_limiter_allows_up_to_the_limit_then_denies():
    limiter = MemoryRateLimiter(100)

    first = await limiter.hit("http-ip", "ip-a", limit=2, window_seconds=WINDOW)
    second = await limiter.hit("http-ip", "ip-a", limit=2, window_seconds=WINDOW)
    third = await limiter.hit("http-ip", "ip-a", limit=2, window_seconds=WINDOW)

    assert (first.allowed, first.remaining) == (True, 1)
    assert (second.allowed, second.remaining) == (True, 0)
    assert (third.allowed, third.remaining) == (False, 0)
    assert third.retry_after >= 1


@pytest.mark.asyncio
async def test_memory_limiter_counts_namespaces_and_identifiers_separately():
    limiter = MemoryRateLimiter(100)

    assert (await limiter.hit("http-ip", "shared", limit=1, window_seconds=WINDOW)).allowed
    assert not (await limiter.hit("http-ip", "shared", limit=1, window_seconds=WINDOW)).allowed
    # Same identifier, different namespace -> its own counter.
    assert (await limiter.hit("http-principal", "shared", limit=1, window_seconds=WINDOW)).allowed
    # Different identifier, same namespace -> its own counter.
    assert (await limiter.hit("http-ip", "other", limit=1, window_seconds=WINDOW)).allowed


@pytest.mark.asyncio
async def test_memory_limiter_window_expires_and_resets_the_counter():
    now = {"t": 0.0}
    limiter = MemoryRateLimiter(100, clock=lambda: now["t"])

    assert (await limiter.hit("http-ip", "ip-a", limit=1, window_seconds=WINDOW)).allowed
    exhausted = await limiter.hit("http-ip", "ip-a", limit=1, window_seconds=WINDOW)
    assert not exhausted.allowed
    assert exhausted.retry_after == WINDOW

    now["t"] = float(WINDOW)
    assert (await limiter.hit("http-ip", "ip-a", limit=1, window_seconds=WINDOW)).allowed


@pytest.mark.asyncio
async def test_memory_limiter_refuses_new_identifiers_at_the_bucket_ceiling():
    limiter = MemoryRateLimiter(3)

    for index in range(3):
        assert (await limiter.hit("http-ip", f"ip-{index}", limit=9, window_seconds=WINDOW)).allowed

    overflow = await limiter.hit("http-ip", "ip-new", limit=9, window_seconds=WINDOW)
    assert overflow == RateLimitDecision(allowed=False, retry_after=WINDOW, remaining=0)
    # The map is bounded: an unbounded identifier space cannot grow it.
    assert len(limiter._buckets) == 3
    # Identifiers already tracked keep being served.
    assert (await limiter.hit("http-ip", "ip-0", limit=9, window_seconds=WINDOW)).allowed
    assert len(limiter._buckets) == 3


@pytest.mark.asyncio
async def test_memory_limiter_reclaims_expired_buckets_before_refusing():
    now = {"t": 0.0}
    limiter = MemoryRateLimiter(2, clock=lambda: now["t"])

    await limiter.hit("http-ip", "ip-0", limit=9, window_seconds=WINDOW)
    await limiter.hit("http-ip", "ip-1", limit=9, window_seconds=WINDOW)
    assert not (await limiter.hit("http-ip", "ip-2", limit=9, window_seconds=WINDOW)).allowed

    now["t"] = WINDOW + 1.0
    assert (await limiter.hit("http-ip", "ip-2", limit=9, window_seconds=WINDOW)).allowed
    assert len(limiter._buckets) == 1


# --- redis adapter -----------------------------------------------------------


@pytest.mark.asyncio
async def test_redis_limiter_maps_counter_and_ttl_onto_a_decision():
    client = _FakeRedis(ttl=42)
    limiter = RedisRateLimiter("redis://unused", client=client)

    first = await limiter.hit("http-ip", "ip-a", limit=2, window_seconds=WINDOW)
    second = await limiter.hit("http-ip", "ip-a", limit=2, window_seconds=WINDOW)
    third = await limiter.hit("http-ip", "ip-a", limit=2, window_seconds=WINDOW)

    assert (first.allowed, first.remaining, first.retry_after) == (True, 1, 42)
    assert (second.allowed, second.remaining) == (True, 0)
    assert (third.allowed, third.remaining) == (False, 0)
    # Counters are shared across replicas because the key is derived from the identifier.
    assert list(client.counts) == [rate_limit._counter_key("http-ip", "ip-a")]

    assert await limiter.ping()
    await limiter.aclose()
    assert client.closed


@pytest.mark.asyncio
async def test_redis_limiter_falls_back_to_the_window_when_no_ttl_is_reported():
    limiter = RedisRateLimiter("redis://unused", client=_FakeRedis(ttl=-1))

    decision = await limiter.hit("http-ip", "ip-a", limit=1, window_seconds=WINDOW)

    assert decision.retry_after == WINDOW


def test_limiter_selection_follows_the_session_backend(monkeypatch):
    monkeypatch.setattr(rate_limit, "get_settings", lambda: Settings(_env_file=None))
    rate_limit.get_rate_limiter.cache_clear()
    try:
        memory = rate_limit.get_rate_limiter()
        assert isinstance(memory, MemoryRateLimiter)
        assert memory.max_buckets == 10_000

        monkeypatch.setattr(
            rate_limit,
            "get_settings",
            lambda: Settings(
                _env_file=None,
                session_backend="redis",
                redis_url="redis://localhost:6379/0",
            ),
        )
        rate_limit.get_rate_limiter.cache_clear()
        assert isinstance(rate_limit.get_rate_limiter(), RedisRateLimiter)
    finally:
        rate_limit.get_rate_limiter.cache_clear()


# --- failure policy ----------------------------------------------------------


@pytest.mark.asyncio
async def test_disabled_rate_limiting_never_touches_the_backend(monkeypatch):
    limiter = _CountingLimiter()
    monkeypatch.setattr(rate_limit, "get_rate_limiter", lambda: limiter)

    decision = await check_limit(
        "http-ip",
        "203.0.113.9",
        limit=7,
        settings=Settings(_env_file=None, rate_limit_enabled=False),
    )

    assert decision == RateLimitDecision(allowed=True, retry_after=0, remaining=7)
    assert limiter.calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_open", [False, True])
async def test_backend_errors_honor_the_configured_failure_policy(monkeypatch, fail_open):
    monkeypatch.setattr(rate_limit, "get_rate_limiter", lambda: _BrokenLimiter())
    settings = Settings(_env_file=None, rate_limit_fail_open=fail_open)

    decision = await check_limit("http-ip", "203.0.113.9", limit=5, settings=settings)

    assert decision.allowed is fail_open
    assert decision.retry_after == settings.rate_limit_window_seconds
    assert decision.remaining == 0


@pytest.mark.asyncio
async def test_check_limit_defaults_to_fail_closed(monkeypatch):
    monkeypatch.setattr(rate_limit, "get_settings", lambda: Settings(_env_file=None))
    monkeypatch.setattr(rate_limit, "get_rate_limiter", lambda: _BrokenLimiter())

    assert not (await check_limit("http-ip", "203.0.113.9", limit=5)).allowed


# --- principal dependencies --------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "dependency",
    [rate_limited_customer_principal, rate_limited_admin_principal],
)
async def test_principal_dependencies_raise_429_with_retry_after(limited, dependency):
    limited(rate_limit_http_principal_requests=2)
    principal = Principal("subject-a", "customer-1", frozenset({"customer", "assured-admin"}))

    assert await dependency(principal) is principal
    assert await dependency(principal) is principal
    with pytest.raises(HTTPException) as exc_info:
        await dependency(principal)

    assert exc_info.value.status_code == 429
    assert exc_info.value.detail == "Too many requests"
    assert int(exc_info.value.headers["Retry-After"]) >= 1


@pytest.mark.asyncio
async def test_principal_budgets_are_isolated_per_subject(limited):
    limited(rate_limit_http_principal_requests=1)
    first = Principal("subject-a", "customer-1", frozenset({"customer"}))
    second = Principal("subject-b", "customer-2", frozenset({"customer"}))

    await rate_limited_customer_principal(first)
    with pytest.raises(HTTPException):
        await rate_limited_customer_principal(first)
    # A different subject still has its own budget.
    assert await rate_limited_customer_principal(second) is second


@pytest.mark.asyncio
async def test_per_ip_and_per_principal_budgets_are_independent(limited):
    limited(rate_limit_http_ip_requests=1, rate_limit_http_principal_requests=5)
    principal = Principal("203.0.113.9", "customer-1", frozenset({"customer"}))

    assert (await rate_limit.http_ip_limit(_connection("203.0.113.9"))).allowed
    assert not (await rate_limit.http_ip_limit(_connection("203.0.113.9"))).allowed
    # Same string used as a subject: the exhausted IP budget does not bleed across.
    assert await rate_limited_customer_principal(principal) is principal


# --- websocket gates ---------------------------------------------------------


@pytest.mark.asyncio
async def test_websocket_connection_limits_apply_per_ip(limited):
    limited(rate_limit_websocket_ip_connections=2)

    assert await websocket_ip_connection_allowed(_connection("198.51.100.1"))
    assert await websocket_ip_connection_allowed(_connection("198.51.100.1"))
    assert not await websocket_ip_connection_allowed(_connection("198.51.100.1"))
    assert await websocket_ip_connection_allowed(_connection("198.51.100.2"))


@pytest.mark.asyncio
async def test_websocket_connection_limits_apply_per_principal(limited):
    limited(rate_limit_websocket_principal_connections=1)
    principal = Principal("subject-a", "customer-1", frozenset({"customer"}))
    other = Principal("subject-b", "customer-2", frozenset({"customer"}))

    assert await websocket_principal_connection_allowed(principal)
    assert not await websocket_principal_connection_allowed(principal)
    assert await websocket_principal_connection_allowed(other)


@pytest.mark.asyncio
async def test_websocket_action_limit_rejects_on_the_ip_dimension(limited):
    limited(rate_limit_websocket_ip_actions=1, rate_limit_websocket_principal_actions=50)
    ws = _connection("198.51.100.1")
    principal = Principal("subject-a", "customer-1", frozenset({"customer"}))

    assert await websocket_action_allowed(ws, principal)
    assert not await websocket_action_allowed(ws, principal)
    # The IP is what ran out, so a different peer with the same subject still works.
    assert await websocket_action_allowed(_connection("198.51.100.2"), principal)


@pytest.mark.asyncio
async def test_websocket_action_limit_rejects_on_the_principal_dimension(limited):
    limited(rate_limit_websocket_ip_actions=50, rate_limit_websocket_principal_actions=1)
    principal = Principal("subject-a", "customer-1", frozenset({"customer"}))

    assert await websocket_action_allowed(_connection("198.51.100.1"), principal)
    # Rotating the source IP does not buy more expensive turns for the same subject.
    assert not await websocket_action_allowed(_connection("198.51.100.2"), principal)


# --- HTTP middleware ---------------------------------------------------------


def test_http_burst_past_the_ip_limit_returns_a_rate_limited_body(api_client, limited):
    limited(rate_limit_http_ip_requests=3)

    for _ in range(3):
        assert api_client.get("/api/payments/config").status_code == 200

    blocked = api_client.get("/api/payments/config")
    assert blocked.status_code == 429
    assert blocked.json() == {"error": "rate_limited", "message": "Too many requests."}
    assert int(blocked.headers["Retry-After"]) >= 1


def test_requests_under_the_http_ip_limit_pass_through_untouched(api_client, limited):
    limited(rate_limit_http_ip_requests=5)

    for _ in range(4):
        response = api_client.get("/api/payments/config")
        assert response.status_code == 200
        assert "Retry-After" not in response.headers


def test_health_probes_and_cors_preflight_bypass_the_http_limit(api_client, limited):
    limited(rate_limit_http_ip_requests=1)
    assert api_client.get("/api/payments/config").status_code == 200
    assert api_client.get("/api/payments/config").status_code == 429

    # Availability during an incident: probes and preflights stay unlimited.
    assert api_client.get("/health").status_code == 200
    preflight = api_client.options(
        "/api/chat",
        headers={"origin": "http://localhost:5173", "access-control-request-method": "POST"},
    )
    assert preflight.status_code == 200


def test_disabled_rate_limiting_bypasses_the_http_gate(api_client, limited):
    limited(rate_limit_enabled=False, rate_limit_http_ip_requests=1)

    for _ in range(5):
        assert api_client.get("/api/payments/config").status_code == 200


def test_admin_routes_enforce_the_per_principal_http_budget(api_client, limited):
    limited(rate_limit_http_ip_requests=1000, rate_limit_http_principal_requests=2)

    assert api_client.get("/api/admin/demo-customers").status_code == 200
    assert api_client.get("/api/admin/demo-customers").status_code == 200

    blocked = api_client.get("/api/admin/demo-customers")
    assert blocked.status_code == 429
    assert blocked.json() == {"detail": "Too many requests"}
    assert int(blocked.headers["Retry-After"]) >= 1


# --- websocket routes end to end ---------------------------------------------


def test_websocket_connection_over_the_ip_limit_is_closed(api_client, limited):
    limited(rate_limit_websocket_ip_connections=1)

    with api_client.websocket_connect("/api/ws/chat"):
        pass

    with (
        pytest.raises(WebSocketDisconnect) as exc_info,
        api_client.websocket_connect("/api/ws/chat"),
    ):
        pass
    assert exc_info.value.code == 1013


def test_voice_websocket_connection_over_the_ip_limit_is_closed(api_client, limited):
    limited(rate_limit_websocket_ip_connections=1)

    with api_client.websocket_connect("/ws/voice"):
        pass

    with (
        pytest.raises(WebSocketDisconnect) as exc_info,
        api_client.websocket_connect("/ws/voice"),
    ):
        pass
    assert exc_info.value.code == 1013


def test_websocket_action_over_the_limit_closes_the_socket(api_client, limited):
    limited(rate_limit_websocket_ip_connections=10, rate_limit_websocket_ip_actions=1)

    with (
        pytest.raises(WebSocketDisconnect) as exc_info,
        api_client.websocket_connect("/api/ws/chat") as ws,
    ):
        # The action budget is spent before the payload is even validated.
        ws.send_json({"message": ""})
        assert ws.receive_json()["code"] == "invalid_request"
        ws.send_json({"message": ""})
        ws.receive_json()
    assert exc_info.value.code == 1013


# --- readiness + production configuration ------------------------------------


def test_readiness_reports_503_when_the_rate_limit_store_is_unreachable(api_client, monkeypatch):
    from insurance_ai.api import routes_health

    monkeypatch.setattr(routes_health, "get_rate_limiter", lambda: _BrokenLimiter())
    response = api_client.get("/ready")

    assert response.status_code == 503
    assert response.json() == {"ready": False}


@pytest.mark.parametrize(
    ("override", "reason"),
    [
        ({"rate_limit_enabled": False}, "rate limiting is disabled"),
        ({"rate_limit_fail_open": True}, "rate limiting is configured to fail open"),
    ],
)
def test_production_rejects_unsafe_rate_limit_configuration(override, reason):
    with pytest.raises(ValidationError, match=reason):
        _production_settings(**override)


def test_production_accepts_enabled_fail_closed_rate_limiting():
    settings = _production_settings(rate_limit_enabled=True, rate_limit_fail_open=False)

    assert settings.rate_limit_enabled
    assert not settings.rate_limit_fail_open
