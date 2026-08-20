"""Typed application configuration.

Configuration is validated at startup (see ``settings.validate_runtime``) so that
missing or contradictory settings fail loudly with a clear message rather than at
first use. Nothing secret is ever defaulted to a real value.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

HardwareProfile = Literal["local-cpu", "apple-silicon", "nvidia", "cloud-gpu"]
LLMProviderName = Literal["mock", "openai", "ollama", "huggingface"]
STTProviderName = Literal["mock", "faster-whisper"]
TTSProviderName = Literal["mock", "piper", "kokoro"]
EmbeddingProviderName = Literal["mock", "hash", "sentence-transformers", "openai", "ollama"]
VectorBackend = Literal["numpy", "pgvector"]
PaymentProviderName = Literal["mock", "stripe"]
TelephonyProviderName = Literal["none", "twilio"]


class Settings(BaseSettings):
    """Central settings object, populated from environment / ``.env``."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="INSURANCE_AI_",
        extra="ignore",
        env_nested_delimiter="__",
    )

    # --- runtime -----------------------------------------------------------
    environment: Literal["dev", "test", "prod"] = "dev"
    hardware_profile: HardwareProfile = "local-cpu"
    log_level: str = "INFO"
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    # Optional OpenTelemetry tracing. When enabled, the API is auto-instrumented and
    # spans export via OTLP to the standard OTEL_EXPORTER_OTLP_ENDPOINT (or the default
    # http://localhost:4318). Off by default; requires the `otel` extra.
    otel_enabled: bool = False
    otel_service_name: str = "insurance-ai-api"

    # --- database ----------------------------------------------------------
    # Async SQLAlchemy URL. Default points at the compose Postgres service.
    database_url: str = "postgresql+asyncpg://insurance:insurance@localhost:5432/insurance_ai"

    # --- providers (which implementation runs) -----------------------------
    llm_provider: LLMProviderName = "mock"
    stt_provider: STTProviderName = "mock"
    tts_provider: TTSProviderName = "mock"
    embedding_provider: EmbeddingProviderName = "hash"
    vector_backend: VectorBackend = "numpy"
    payment_provider: PaymentProviderName = "mock"
    telephony_provider: TelephonyProviderName = "none"

    # --- model ids (overridable, HF-style) ---------------------------------
    llm_model: str = "mock-insurance-llm"
    stt_model: str = "Systran/faster-whisper-base.en"
    tts_voice: str = "en_US-amy-medium"
    embedding_model: str = "hash-512"
    embedding_dim: int = 512

    # --- external provider endpoints / keys (optional) ---------------------
    openai_api_key: str | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    ollama_base_url: str = "http://localhost:11434"
    huggingface_token: str | None = None

    # --- payments ----------------------------------------------------------
    stripe_secret_key: str | None = None
    stripe_webhook_secret: str | None = None

    # --- telephony ---------------------------------------------------------
    twilio_account_sid: str | None = None
    twilio_auth_token: str | None = None
    public_base_url: str | None = None  # for Twilio webhooks / media streams

    # --- rag ---------------------------------------------------------------
    rag_chunk_size: int = 700
    rag_chunk_overlap: int = 120
    rag_top_k: int = 4
    rag_min_score: float = 0.22
    # Optional reranking of the retrieved candidate pool before taking top_k.
    rag_rerank: Literal["none", "lexical", "cross-encoder"] = "none"
    rag_candidate_pool: int = 12  # how many candidates to rerank over
    rag_reranker_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"

    # --- security ----------------------------------------------------------
    verification_max_attempts: int = 3
    otp_code_for_demo: str = "123456"  # deterministic demo OTP (synthetic)
    session_ttl_seconds: int = 3600

    @model_validator(mode="after")
    def _check_provider_credentials(self) -> Settings:
        """Fail fast when a provider is selected without its credentials."""
        if self.llm_provider == "openai" and not self.openai_api_key:
            raise ValueError("llm_provider=openai requires INSURANCE_AI_OPENAI_API_KEY")
        if self.payment_provider == "stripe" and not self.stripe_secret_key:
            raise ValueError("payment_provider=stripe requires INSURANCE_AI_STRIPE_SECRET_KEY")
        if self.telephony_provider == "twilio" and not (
            self.twilio_account_sid and self.twilio_auth_token
        ):
            raise ValueError("telephony_provider=twilio requires Twilio SID + auth token")
        return self

    @property
    def is_stripe_enabled(self) -> bool:
        return self.payment_provider == "stripe" and bool(self.stripe_secret_key)

    @property
    def is_telephony_enabled(self) -> bool:
        return self.telephony_provider == "twilio" and bool(self.twilio_account_sid)


@lru_cache
def get_settings() -> Settings:
    return Settings()
