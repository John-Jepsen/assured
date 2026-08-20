"""ASGI entrypoint: `uvicorn insurance_ai.main:app`."""

from insurance_ai.api.app import app

__all__ = ["app"]
