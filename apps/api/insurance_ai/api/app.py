"""FastAPI application factory."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from insurance_ai import __version__
from insurance_ai.config import get_settings
from insurance_ai.observability import configure_logging, get_logger, setup_tracing
from insurance_ai.tools.registry import load_all_tools

log = get_logger("api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    load_all_tools()
    log.info(
        "startup",
        version=__version__,
        providers={
            "llm": get_settings().llm_provider,
            "stt": get_settings().stt_provider,
            "tts": get_settings().tts_provider,
        },
    )
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="Assured — Multimodal Voice-to-Voice Insurance AI",
        version=__version__,
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    from insurance_ai.api import (
        routes_admin,
        routes_chat,
        routes_health,
        routes_payments,
        routes_telephony,
        routes_voice,
    )

    app.include_router(routes_health.router)
    app.include_router(routes_chat.router)
    app.include_router(routes_voice.router)
    app.include_router(routes_admin.router)
    app.include_router(routes_payments.router)
    app.include_router(routes_telephony.router)

    setup_tracing(app)  # no-op unless INSURANCE_AI_OTEL_ENABLED=true + `otel` extra

    @app.exception_handler(Exception)
    async def _unhandled(_request, exc):  # never leak a stack trace
        log.error("unhandled_error", error=type(exc).__name__)
        return JSONResponse(
            status_code=500,
            content={"error": "internal_error", "message": "Something went wrong on our side."},
        )

    return app


app = create_app()
