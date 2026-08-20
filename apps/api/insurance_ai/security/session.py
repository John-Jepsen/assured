"""Per-conversation session state (verification, bound customer, attempts).

Kept separate from the raw transcript. Verification state is authoritative here and
mirrored onto the Conversation row for the admin view. One conversation is bound to
at most one customer — cross-customer access is impossible by construction.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from insurance_ai.domain.enums import VerificationStatus


@dataclass
class Session:
    conversation_id: str
    customer_id: str | None = None
    verification_status: VerificationStatus = VerificationStatus.UNVERIFIED
    attempts: int = 0
    # factors the caller has satisfied this session (audit trail, non-secret labels)
    satisfied_factors: list[str] = field(default_factory=list)
    escalated: bool = False

    @property
    def is_verified(self) -> bool:
        return self.verification_status == VerificationStatus.VERIFIED


class SessionStore:
    """In-memory session registry. Process-local; conversation state persists in DB."""

    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}

    def get(self, conversation_id: str) -> Session:
        if conversation_id not in self._sessions:
            self._sessions[conversation_id] = Session(conversation_id=conversation_id)
        return self._sessions[conversation_id]

    def reset(self, conversation_id: str) -> None:
        self._sessions.pop(conversation_id, None)


_STORE = SessionStore()


def get_session_store() -> SessionStore:
    return _STORE
