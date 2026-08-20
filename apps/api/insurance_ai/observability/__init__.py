"""Structured logging + latency timing helpers."""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager

import structlog

from insurance_ai.config import get_settings

_configured = False

_SENSITIVE = {
    "otp_code",
    "date_of_birth",
    "card_number",
    "cvv",
    "ssn",
    "auth_token",
    "api_key",
    "secret",
    "password",
}


def _mask_processor(_logger, _method, event_dict):
    for key in list(event_dict):
        if any(s in key.lower() for s in _SENSITIVE):
            event_dict[key] = "***"
    return event_dict


def configure_logging() -> None:
    global _configured
    if _configured:
        return
    settings = get_settings()
    logging.basicConfig(level=settings.log_level, format="%(message)s")
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            _mask_processor,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, settings.log_level.upper(), logging.INFO)
        ),
    )
    _configured = True


def get_logger(name: str = "insurance_ai"):
    configure_logging()
    return structlog.get_logger(name)


def setup_tracing(app) -> bool:
    """Enable OpenTelemetry auto-instrumentation if configured. Never fatal.

    Returns True if tracing was set up. Requires the `otel` extra; if the packages
    are missing it logs a clear hint and continues (observability stays best-effort).
    """
    settings = get_settings()
    if not settings.otel_enabled:
        return False
    log = get_logger("otel")
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        log.warning("otel_libs_missing", hint="pip install -e '.[otel]' to enable tracing")
        return False
    provider = TracerProvider(
        resource=Resource.create({"service.name": settings.otel_service_name})
    )
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(app)
    log.info("otel_enabled", service=settings.otel_service_name)
    return True


@contextmanager
def timed(store: dict[str, float], key: str):
    start = time.perf_counter()
    try:
        yield
    finally:
        store[key] = (time.perf_counter() - start) * 1000
