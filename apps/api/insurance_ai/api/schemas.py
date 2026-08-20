"""API request/response schemas."""

from __future__ import annotations

from pydantic import BaseModel


class ChatRequest(BaseModel):
    message: str
    conversation_id: str | None = None
    channel: str = "web"


class ChatResponse(BaseModel):
    conversation_id: str
    answer: str
    needs_verification: bool
    trace: dict


class VerifyRequest(BaseModel):
    conversation_id: str
    last_name: str | None = None
    zip_code: str | None = None
    policy_number: str | None = None
    date_of_birth: str | None = None
    otp_code: str | None = None


class HealthStatus(BaseModel):
    status: str
    version: str
    providers: dict
    database: str
    features: dict
