"""API request/response schemas."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_validator

MAX_CHAT_MESSAGE_CHARS = 8_000
MAX_CONVERSATION_ID_CHARS = 64

ConversationId = Annotated[str, Field(min_length=1, max_length=MAX_CONVERSATION_ID_CHARS)]
ChatMessage = Annotated[str, Field(min_length=1, max_length=MAX_CHAT_MESSAGE_CHARS)]


class ChatRequest(BaseModel):
    message: ChatMessage
    conversation_id: ConversationId | None = None
    channel: Literal["web", "phone"] = "web"

    @field_validator("message")
    @classmethod
    def _message_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("message must not be blank")
        return value


class ChatSocketRequest(BaseModel):
    """Validated client frame for the streaming chat socket."""

    message: ChatMessage
    conversation_id: ConversationId | None = None

    @field_validator("message")
    @classmethod
    def _message_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("message must not be blank")
        return value


class ChatResponse(BaseModel):
    conversation_id: str
    answer: str
    needs_verification: bool
    trace: dict


class VerifyRequest(BaseModel):
    conversation_id: ConversationId
    last_name: Annotated[str, Field(max_length=100)] | None = None
    zip_code: Annotated[str, Field(max_length=20)] | None = None
    policy_number: Annotated[str, Field(max_length=64)] | None = None
    date_of_birth: Annotated[str, Field(max_length=32)] | None = None
    otp_code: Annotated[str, Field(max_length=32)] | None = None


class HealthStatus(BaseModel):
    status: str
    version: str
    providers: dict
    database: str
    features: dict
