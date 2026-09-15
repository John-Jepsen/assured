"""Typed application configuration.

Configuration is validated at startup (see ``settings.validate_runtime``) so that
missing or contradictory settings fail loudly with a clear message rather than at
first use. Nothing secret is ever defaulted to a real value.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

HardwareProfile = Literal["local-cpu", "apple-silicon", "nvidia", "cloud-gpu"]
LLMProviderName = Literal["mock", "openai", "ollama", "huggingface"]
STTProviderName = Literal["mock", "faster-whisper"]
TTSProviderName = Literal["mock", "piper", "kokoro"]
EmbeddingProviderName = Literal["mock", "hash", "sentence-transformers", "openai", "ollama"]
VectorBackend = Literal["numpy", "pgvector"]
PaymentProviderName = Literal["mock", "stripe"]
TelephonyProviderName = Literal["none", "twilio"]
SessionBackend = Literal["memory", "redis"]


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
    allowed_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:5173", "http://localhost:8080"]
    )
    demo_features_enabled: bool = True
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
    # OpenAI-compatible endpoint exposed by a dedicated Hugging Face/vLLM
    # deployment. Model weights are deliberately not loaded in the API process.
    huggingface_base_url: str = "http://localhost:8001/v1"
    huggingface_token: str | None = None
    huggingface_model_revision: str | None = None
    llm_connect_timeout_seconds: float = Field(default=10.0, gt=0)
    llm_request_timeout_seconds: float = Field(default=120.0, gt=0)
    llm_max_retries: int = Field(default=2, ge=0, le=10)
    llm_retry_backoff_seconds: float = Field(default=0.25, ge=0, le=60)
    llm_max_connections: int = Field(default=100, gt=0)
    llm_max_keepalive_connections: int = Field(default=20, ge=0)
    llm_max_tokens: int = Field(default=400, gt=0)

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
    otp_code_for_demo: str | None = "123456"  # deterministic demo OTP (synthetic)
    session_backend: SessionBackend = "memory"
    redis_url: str | None = None
    session_ttl_seconds: int = Field(default=3600, gt=0)
    session_lock_timeout_seconds: int = Field(default=180, gt=0)
    session_lock_blocking_timeout_seconds: int = Field(default=10, gt=0)

    @field_validator("allowed_origins")
    @classmethod
    def _validate_origins(cls, origins: list[str]) -> list[str]:
        normalized: list[str] = []
        for origin in origins:
            parsed = urlsplit(origin)
            if (
                origin == "*"
                or parsed.scheme not in {"http", "https"}
                or not parsed.netloc
                or parsed.path not in {"", "/"}
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError(
                    "allowed_origins must contain exact http(s) origins without paths or wildcards"
                )
            normalized.append(origin.rstrip("/"))
        return normalized

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
        if self.llm_max_keepalive_connections > self.llm_max_connections:
            raise ValueError("llm_max_keepalive_connections cannot exceed llm_max_connections")
        if self.session_backend == "redis" and not self.redis_url:
            raise ValueError("session_backend=redis requires INSURANCE_AI_REDIS_URL")
        if self.llm_provider == "huggingface" and self.environment == "prod":
            if not self.huggingface_token:
                raise ValueError("production Hugging Face serving requires a model-service token")
            if not self.huggingface_model_revision:
                raise ValueError("production Hugging Face serving requires an immutable revision")

        if self.environment == "prod":
            unsafe: list[str] = []
            if self.demo_features_enabled:
                unsafe.append("demo features are enabled")
            if self.otp_code_for_demo:
                unsafe.append("the demo OTP is configured")
            if not self.allowed_origins:
                unsafe.append("no browser origins are configured")
            if any(urlsplit(origin).scheme != "https" for origin in self.allowed_origins):
                unsafe.append("browser origins must use HTTPS")
            if self.llm_provider == "mock":
                unsafe.append("the mock LLM is selected")
            if self.stt_provider == "mock" or self.tts_provider == "mock":
                unsafe.append("mock speech is selected")
            if self.embedding_provider in {"mock", "hash"}:
                unsafe.append("a demo embedding provider is selected")
            if self.vector_backend != "pgvector":
                unsafe.append("pgvector is not selected")
            if self.payment_provider == "mock":
                unsafe.append("the mock payment provider is selected")
            if self.payment_provider == "stripe" and not self.stripe_webhook_secret:
                unsafe.append("the Stripe webhook secret is missing")
            if self.telephony_provider != "none":
                unsafe.append(
                    "telephony cannot be enabled before webhook signature validation is installed"
                )
            if self.session_backend != "redis":
                unsafe.append("the process-local session backend is selected")

            redis = urlsplit(self.redis_url or "")
            if self.session_backend == "redis" and redis.scheme not in {"redis", "rediss"}:
                unsafe.append("a valid Redis URL is required")
            if redis.password in {None, "", "insurance-demo"}:
                unsafe.append("Redis credentials are missing or use the demo default")

            database = urlsplit(self.database_url.replace("postgresql+asyncpg", "postgresql", 1))
            if database.scheme not in {"postgresql", "postgres"}:
                unsafe.append("PostgreSQL is required")
            if (database.username, database.password) == ("insurance", "insurance"):
                unsafe.append("default database credentials are configured")

            if unsafe:
                raise ValueError("unsafe production configuration: " + "; ".join(unsafe))
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
