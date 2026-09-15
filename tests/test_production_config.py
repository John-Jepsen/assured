"""Fail-closed production settings and exact browser-origin tests."""

from __future__ import annotations

import pytest
from insurance_ai.config import Settings
from insurance_ai.security.origin import websocket_origin_allowed
from pydantic import ValidationError
from starlette.websockets import WebSocketDisconnect


def _production_settings(**overrides) -> Settings:
    values = {
        "_env_file": None,
        "environment": "prod",
        "allowed_origins": ["https://app.assured.example"],
        "demo_features_enabled": False,
        "otp_code_for_demo": None,
        "database_url": "postgresql+asyncpg://assured_app:strong-secret@db.internal/assured",
        "llm_provider": "huggingface",
        "llm_model": "Qwen/Qwen2.5-1.5B-Instruct",
        "huggingface_base_url": "http://vllm.internal:8000/v1",
        "huggingface_token": "internal-model-token",
        "huggingface_model_revision": "immutable-model-revision",
        "stt_provider": "faster-whisper",
        "tts_provider": "piper",
        "embedding_provider": "sentence-transformers",
        "vector_backend": "pgvector",
        "payment_provider": "stripe",
        "stripe_secret_key": "sk_test_placeholder",
        "stripe_webhook_secret": "whsec_placeholder",
        "telephony_provider": "none",
    }
    values.update(overrides)
    return Settings(**values)


def test_safe_production_configuration_is_accepted():
    settings = _production_settings()
    assert settings.environment == "prod"
    assert settings.allowed_origins == ["https://app.assured.example"]


def test_production_defaults_fail_closed_with_actionable_reasons():
    with pytest.raises(ValidationError) as exc_info:
        Settings(
            environment="prod",
            database_url=(
                "postgresql+asyncpg://insurance:insurance@localhost:5432/insurance_ai"
            ),
            _env_file=None,
        )

    message = str(exc_info.value)
    assert "demo features are enabled" in message
    assert "the mock LLM is selected" in message
    assert "default database credentials are configured" in message


@pytest.mark.parametrize(
    ("override", "reason"),
    [
        ({"allowed_origins": ["http://app.assured.example"]}, "must use HTTPS"),
        ({"demo_features_enabled": True}, "demo features are enabled"),
        ({"otp_code_for_demo": "123456"}, "demo OTP"),
        ({"vector_backend": "numpy"}, "pgvector is not selected"),
        ({"stripe_webhook_secret": None}, "Stripe webhook secret is missing"),
        ({"telephony_provider": "twilio", "twilio_account_sid": "AC1", "twilio_auth_token": "token"}, "telephony cannot be enabled"),
    ],
)
def test_production_rejects_unsafe_combinations(override, reason):
    with pytest.raises(ValidationError, match=reason):
        _production_settings(**override)


def test_origins_must_be_exact_and_cannot_use_wildcards():
    with pytest.raises(ValidationError, match="without paths or wildcards"):
        Settings(allowed_origins=["*"], _env_file=None)
    with pytest.raises(ValidationError, match="without paths or wildcards"):
        Settings(allowed_origins=["https://app.assured.example/a/path"], _env_file=None)


def test_websocket_origin_policy_is_exact_and_prod_requires_origin():
    settings = _production_settings()
    assert websocket_origin_allowed("https://app.assured.example", settings)
    assert not websocket_origin_allowed("https://evil.example", settings)
    assert not websocket_origin_allowed(None, settings)
    assert websocket_origin_allowed(None, Settings(_env_file=None))


def test_browser_websocket_rejects_untrusted_origin(api_client):
    with (
        pytest.raises(WebSocketDisconnect) as exc_info,
        api_client.websocket_connect(
            "/api/ws/chat", headers={"origin": "https://evil.example"}
        ),
    ):
        pass
    assert exc_info.value.code == 1008


def test_http_cors_only_echoes_a_configured_origin(api_client):
    trusted = api_client.options(
        "/api/chat",
        headers={
            "origin": "http://localhost:5173",
            "access-control-request-method": "POST",
        },
    )
    assert trusted.status_code == 200
    assert trusted.headers["access-control-allow-origin"] == "http://localhost:5173"

    untrusted = api_client.options(
        "/api/chat",
        headers={
            "origin": "https://evil.example",
            "access-control-request-method": "POST",
        },
    )
    assert untrusted.status_code == 400
    assert "access-control-allow-origin" not in untrusted.headers


def test_demo_customer_directory_can_be_disabled(api_client, monkeypatch):
    from insurance_ai.api import routes_admin

    monkeypatch.setattr(
        routes_admin,
        "get_settings",
        lambda: Settings(demo_features_enabled=False, _env_file=None),
    )
    assert api_client.get("/api/admin/demo-customers").status_code == 404
