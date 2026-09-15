"""Observability: Prometheus primitives, the RED middleware, and the /metrics endpoint.

The metrics registry is a module-level singleton shared by the whole process, so every
test that inspects exposition output clears it first (``_reset_registry``) and again on
teardown, keeping this module from leaking series into (or inheriting them from) the rest
of the suite. Settings are ``lru_cache``d, so the endpoint's configuration is swapped the
way the rest of the suite does it: monkeypatch the route module's ``get_settings`` with a
fresh ``Settings(..., _env_file=None)``.
"""

from __future__ import annotations

import math
import threading

import pytest
from insurance_ai.config import Settings
from insurance_ai.observability.metrics import (
    REGISTRY,
    Counter,
    Gauge,
    Histogram,
    MetricsRegistry,
)

TOKEN = "metrics-test-token"
AUTH = {"authorization": f"Bearer {TOKEN}"}


@pytest.fixture(autouse=True)
def _reset_registry():
    """Keep the process-global registry from leaking values across tests."""
    REGISTRY.clear()
    yield
    REGISTRY.clear()


@pytest.fixture
def client():
    """TestClient over the real ASGI app.

    Most of this module exercises the middleware and the endpoint, neither of which
    touches the database, so this fixture deliberately skips the drop/create/seed cycle
    that ``api_client`` performs on the shared global engine. The two tests that need
    seeded records ask for ``api_client`` instead.
    """
    from fastapi.testclient import TestClient
    from insurance_ai.api.app import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def metrics_on(monkeypatch):
    """Enable the endpoint with a known token, without touching cached global settings."""
    from insurance_ai.api import routes_metrics

    monkeypatch.setattr(
        routes_metrics,
        "get_settings",
        lambda: Settings(metrics_enabled=True, metrics_token=TOKEN, _env_file=None),
    )
    return routes_metrics


def _samples(text: str, prefix: str) -> dict[str, float]:
    """Parse exposition text into {series-with-labels: value} for the given prefix."""
    out: dict[str, float] = {}
    for line in text.splitlines():
        if not line or line.startswith("#") or not line.startswith(prefix):
            continue
        series, _, value = line.rpartition(" ")
        out[series] = float(value)
    return out


# --------------------------------------------------------------------------- primitives


def test_counter_renders_sorted_integer_valued_samples():
    counter = Counter("demo_total", "Demo.", ("method", "route"))
    counter.inc("POST", "/b")
    counter.inc("GET", "/a")
    counter.inc("GET", "/a", amount=2.0)

    assert counter.metric_type == "counter"
    assert counter.render_samples() == [
        'demo_total{method="GET",route="/a"} 3',
        'demo_total{method="POST",route="/b"} 1',
    ]


def test_counter_rejects_wrong_label_arity_and_negative_amounts():
    counter = Counter("demo_total", "Demo.", ("route",))
    with pytest.raises(ValueError, match="requires 1 labels"):
        counter.inc()
    with pytest.raises(ValueError, match="requires 1 labels"):
        counter.inc("/a", "extra")
    with pytest.raises(ValueError, match="cannot be negative"):
        counter.inc("/a", amount=-1.0)


def test_unlabeled_metric_renders_without_a_label_block():
    counter = Counter("demo_total", "Demo.")
    counter.inc()
    assert counter.render_samples() == ["demo_total 1"]


def test_gauge_supports_set_inc_dec_and_advertises_the_gauge_type():
    gauge = Gauge("demo_active", "Demo.", ("method",))
    assert gauge.metric_type == "gauge"

    gauge.inc("GET")
    gauge.inc("GET")
    gauge.dec("GET")
    gauge.set(7.5, "POST")
    assert gauge.render_samples() == [
        'demo_active{method="GET"} 1',
        'demo_active{method="POST"} 7.5',
    ]

    # Unlike Counter, a gauge may legitimately go down (that is what dec() is).
    gauge.dec("GET", amount=3)
    assert 'demo_active{method="GET"} -2' in gauge.render_samples()
    with pytest.raises(ValueError, match="requires 1 labels"):
        gauge.set(1.0)


def test_label_values_are_escaped_for_the_exposition_format():
    counter = Counter("demo_total", "Demo.", ("route",))
    counter.inc('a"b\\c\nd')
    (line,) = counter.render_samples()
    assert line == 'demo_total{route="a\\"b\\\\c\\nd"} 1'
    # The escaped value must not break the single-line, balanced-quote grammar.
    assert line.count("\n") == 0
    assert line.split("{", 1)[1].count('"') - line.count('\\"') == 2


def test_histogram_buckets_are_cumulative_with_matching_sum_and_count():
    histogram = Histogram("demo_seconds", "Demo.", ("route",), (0.1, 0.5, 1.0))
    assert histogram.metric_type == "histogram"
    for value in (0.05, 0.2, 2.0):
        histogram.observe(value, "/a")

    assert histogram.render_samples() == [
        'demo_seconds_bucket{route="/a",le="0.1"} 1',
        'demo_seconds_bucket{route="/a",le="0.5"} 2',
        'demo_seconds_bucket{route="/a",le="1"} 2',
        'demo_seconds_bucket{route="/a",le="+Inf"} 3',
        'demo_seconds_count{route="/a"} 3',
        'demo_seconds_sum{route="/a"} 2.25',
    ]


def test_histogram_buckets_are_deduplicated_and_sorted():
    histogram = Histogram("demo_seconds", "Demo.", (), (1.0, 0.5, 1.0, 0.25))
    assert histogram.buckets == (0.25, 0.5, 1.0)


def test_histogram_rejects_invalid_buckets_and_observations():
    with pytest.raises(ValueError, match="finite and positive"):
        Histogram("demo_seconds", "Demo.", (), (0.0, 1.0))
    with pytest.raises(ValueError, match="finite and positive"):
        Histogram("demo_seconds", "Demo.", (), (math.inf,))

    histogram = Histogram("demo_seconds", "Demo.", ("route",), (1.0,))
    with pytest.raises(ValueError, match="finite and non-negative"):
        histogram.observe(-0.1, "/a")
    with pytest.raises(ValueError, match="finite and non-negative"):
        histogram.observe(math.nan, "/a")
    with pytest.raises(ValueError, match="requires 1 labels"):
        histogram.observe(0.1)


def test_registry_emits_help_and_type_headers_and_rejects_duplicate_names():
    registry = MetricsRegistry()
    counter = registry.register(Counter("demo_total", "Demo counter.", ("route",)))
    registry.register(Histogram("demo_seconds", "Demo histogram.", (), (1.0,)))
    counter.inc("/a")

    text = registry.render()
    assert text.endswith("\n")
    assert "# HELP demo_total Demo counter." in text
    assert "# TYPE demo_total counter" in text
    assert "# TYPE demo_seconds histogram" in text
    # A metric with no observations still advertises its HELP/TYPE headers.
    assert "demo_seconds_count" not in text

    with pytest.raises(ValueError, match="duplicate metric"):
        registry.register(Counter("demo_total", "Demo counter.", ("route",)))


def test_registry_clear_resets_every_registered_metric():
    registry = MetricsRegistry()
    counter = registry.register(Counter("demo_total", "Demo.", ("route",)))
    histogram = registry.register(Histogram("demo_seconds", "Demo.", ("route",), (1.0,)))
    gauge = registry.register(Gauge("demo_active", "Demo.", ("route",)))
    counter.inc("/a")
    histogram.observe(0.5, "/a")
    gauge.set(3, "/a")

    registry.clear()
    assert counter.render_samples() == []
    assert histogram.render_samples() == []
    assert gauge.render_samples() == []


def test_counter_and_histogram_are_thread_safe_under_concurrent_updates():
    counter = Counter("demo_total", "Demo.", ("route",))
    histogram = Histogram("demo_seconds", "Demo.", ("route",), (0.5, 1.0))
    threads_count, per_thread = 8, 500
    barrier = threading.Barrier(threads_count)

    def worker() -> None:
        barrier.wait()
        for _ in range(per_thread):
            counter.inc("/a")
            histogram.observe(0.25, "/a")

    threads = [threading.Thread(target=worker) for _ in range(threads_count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    total = threads_count * per_thread
    assert counter.render_samples() == [f'demo_total{{route="/a"}} {total}']
    assert f'demo_seconds_count{{route="/a"}} {total}' in histogram.render_samples()
    assert f'demo_seconds_bucket{{route="/a",le="+Inf"}} {total}' in histogram.render_samples()


# ---------------------------------------------------------------------------- endpoint


def test_metrics_endpoint_is_404_when_disabled(client):
    """Default settings have metrics_enabled=False; the path must not expose anything."""
    response = client.get("/metrics")
    assert response.status_code == 404
    assert "insurance_ai_http_requests_total" not in response.text

    # Even a caller who knows a token gets nothing back while the feature is off.
    assert client.get("/metrics", headers=AUTH).status_code == 404


def test_metrics_requires_a_bearer_token(client, metrics_on):
    response = client.get("/metrics")
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert "insurance_ai_http_requests_total" not in response.text


def test_metrics_rejects_an_incorrect_authorization_header(client, metrics_on):
    rejected = [
        "Bearer wrong-token",
        f"Bearer {TOKEN}x",  # correct prefix, trailing junk
        f"Bearer {TOKEN[:-1]}",  # correct prefix, truncated
        TOKEN,  # bare token, no scheme
        f"bearer {TOKEN}",  # the scheme comparison is exact
        f"Basic {TOKEN}",
        "",
    ]
    for header in rejected:
        response = client.get("/metrics", headers={"authorization": header})
        assert response.status_code == 401, header
        assert "insurance_ai_http_requests_total" not in response.text


def test_metrics_token_comparison_is_constant_time(client, metrics_on, monkeypatch):
    """The token check must go through secrets.compare_digest, not ``==``."""
    calls: list[tuple[str, str]] = []

    class RecordingSecrets:
        @staticmethod
        def compare_digest(supplied, expected):
            calls.append((supplied, expected))
            return False

    monkeypatch.setattr(metrics_on, "secrets", RecordingSecrets)
    assert client.get("/metrics", headers=AUTH).status_code == 401
    assert calls == [(f"Bearer {TOKEN}", f"Bearer {TOKEN}")]


def test_metrics_returns_prometheus_exposition_text(client, metrics_on):
    REGISTRY.clear()
    client.get("/health")

    response = client.get("/metrics", headers=AUTH)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain; version=0.0.4")

    text = response.text
    assert text.endswith("\n")
    expected = {
        "insurance_ai_http_requests_total": "counter",
        "insurance_ai_http_request_duration_seconds": "histogram",
        "insurance_ai_http_requests_active": "gauge",
        "insurance_ai_provider_requests_total": "counter",
        "insurance_ai_provider_request_duration_seconds": "histogram",
        "insurance_ai_model_queue_rejections_total": "counter",
        "insurance_ai_model_requests_active": "gauge",
    }
    for name, metric_type in expected.items():
        assert f"# TYPE {name} {metric_type}" in text
        assert f"# HELP {name} " in text

    # Every non-comment line is "series value" with a parseable float value.
    for line in text.splitlines():
        if line.startswith("#"):
            continue
        assert line, "exposition must not contain blank lines"
        series, _, value = line.rpartition(" ")
        assert series.startswith("insurance_ai_")
        float(value.replace("+Inf", "inf"))


def test_metrics_endpoint_is_excluded_from_the_openapi_schema(client):
    assert "/metrics" not in client.get("/openapi.json").json()["paths"]


# -------------------------------------------------------------------------- middleware


def test_middleware_records_request_count_and_duration_per_route_method_status(
    client, metrics_on
):
    REGISTRY.clear()
    for _ in range(3):
        assert client.get("/health").status_code == 200
    assert client.post("/api/chat", json={"message": "   "}).status_code == 422

    text = client.get("/metrics", headers=AUTH).text
    requests = "insurance_ai_http_requests_total"
    counts = _samples(text, requests)
    assert counts[f'{requests}{{method="GET",route="/health",status="200"}}'] == 3
    assert counts[f'{requests}{{method="POST",route="/api/chat",status="422"}}'] == 1

    seconds = "insurance_ai_http_request_duration_seconds"
    durations = _samples(text, seconds)
    health = 'method="GET",route="/health"'
    assert durations[f"{seconds}_count{{{health}}}"] == 3
    assert durations[f'{seconds}_bucket{{{health},le="+Inf"}}'] == 3
    assert durations[f"{seconds}_sum{{{health}}}"] > 0

    # The gauge settles back to zero once in-flight requests complete. The /metrics
    # request itself is still in flight while it renders, hence GET == 1.
    active = _samples(text, "insurance_ai_http_requests_active")
    assert active['insurance_ai_http_requests_active{method="POST"}'] == 0
    assert active['insurance_ai_http_requests_active{method="GET"}'] == 1


def test_middleware_counts_server_errors_with_their_status_label(
    client, metrics_on, monkeypatch
):
    from insurance_ai.api import routes_health

    class UnavailableEngine:
        def connect(self):
            raise RuntimeError("database unavailable")

    monkeypatch.setattr(routes_health, "get_engine", lambda: UnavailableEngine())
    REGISTRY.clear()
    assert client.get("/ready").status_code == 503

    requests = "insurance_ai_http_requests_total"
    counts = _samples(client.get("/metrics", headers=AUTH).text, requests)
    assert counts[f'{requests}{{method="GET",route="/ready",status="503"}}'] == 1


def test_route_label_is_the_template_path_not_the_raw_url(api_client, metrics_on):
    conversation_id = api_client.post("/api/chat", json={"message": "hello"}).json()[
        "conversation_id"
    ]
    REGISTRY.clear()
    assert api_client.get(f"/api/admin/conversations/{conversation_id}").status_code == 200

    text = api_client.get("/metrics", headers=AUTH).text
    assert 'route="/api/admin/conversations/{conversation_id}"' in text
    # The high-cardinality identifier must never become a label value.
    assert conversation_id not in text


def test_unmatched_paths_collapse_into_one_low_cardinality_series(client, metrics_on):
    REGISTRY.clear()
    for suffix in ("alpha", "beta", "gamma"):
        assert client.get(f"/no/such/route/{suffix}").status_code == 404

    text = client.get("/metrics", headers=AUTH).text
    requests = "insurance_ai_http_requests_total"
    counts = _samples(text, requests)
    assert counts[f'{requests}{{method="GET",route="__unmatched__",status="404"}}'] == 3
    for suffix in ("alpha", "beta", "gamma"):
        assert suffix not in text


def test_middleware_sets_a_correlation_id_without_labeling_metrics_with_it(client, metrics_on):
    REGISTRY.clear()
    supplied = "req-abcdef0123456789"
    response = client.get("/health", headers={"x-request-id": supplied})
    assert response.headers["x-request-id"] == supplied

    # A rejected (non-conforming) ID is replaced, and neither form reaches the metrics.
    rejected = client.get("/health", headers={"x-request-id": "bad id with spaces"})
    assert rejected.headers["x-request-id"] != "bad id with spaces"

    text = client.get("/metrics", headers=AUTH).text
    assert supplied not in text
    assert rejected.headers["x-request-id"] not in text
    assert "request_id" not in text


def test_metrics_body_leaks_no_identity_factors_secrets_or_pii(api_client, metrics_on):
    conversation_id = api_client.post(
        "/api/chat", json={"message": "What's my collision deductible on AUTO-10024?"}
    ).json()["conversation_id"]
    verified = api_client.post(
        "/api/verify",
        json={
            "conversation_id": conversation_id,
            "policy_number": "AUTO-10024",
            "zip_code": "78258",
            "otp_code": "123456",
        },
    )
    assert verified.status_code == 200

    text = api_client.get("/metrics", headers=AUTH).text
    for secret in ("AUTO-10024", "78258", "123456", TOKEN, "Bearer", conversation_id):
        assert secret not in text
    # Labels are confined to the stable, enumerable dimensions.
    label_names = {
        pair.split("=", 1)[0]
        for line in text.splitlines()
        if not line.startswith("#") and "{" in line
        for pair in line.split("{", 1)[1].split("}", 1)[0].split(",")
    }
    assert label_names <= {
        "method",
        "route",
        "status",
        "le",
        "provider",
        "operation",
        "outcome",
        "reason",
    }


def test_non_ascii_authorization_header_raises_instead_of_returning_401(client, metrics_on):
    """KNOWN BUG: secrets.compare_digest raises TypeError on non-ASCII str input.

    Header bytes are latin-1 decoded by the ASGI layer, so an unauthenticated caller can
    force an unhandled TypeError out of the route. In the running app the global handler
    turns that into a 500 ("internal_error") instead of a clean 401; under TestClient the
    exception surfaces directly. This pins current behavior and should become a 401
    assertion once the route guards the comparison.
    """
    with pytest.raises(TypeError, match="non-ASCII"):
        client.get("/metrics", headers={b"authorization": "Bearer é".encode("latin-1")})
