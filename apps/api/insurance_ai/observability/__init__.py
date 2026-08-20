"""Structured logging + latency timing helpers."""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager

import structlog

from insurance_ai.config import get_settings

_configured = False

_SENSITIVE = {"otp_code", "date_of_birth", "card_number", "cvv", "ssn", "auth_token",
              "api_key", "secret", "password"}


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


@contextmanager
def timed(store: dict[str, float], key: str):
    start = time.perf_counter()
    try:
        yield
    finally:
        store[key] = (time.perf_counter() - start) * 1000
