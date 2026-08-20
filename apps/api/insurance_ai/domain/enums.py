"""Domain enumerations.

The product set is intentionally open: adding a new insurance product means adding
one enum member plus seed/knowledge data — no architectural change. Nothing in the
agent, tool, or RAG layer is hard-coded to a single product.
"""

from __future__ import annotations

from enum import StrEnum


class ProductType(StrEnum):
    AUTO = "auto"
    HOMEOWNERS = "homeowners"
    RENTERS = "renters"
    LIFE = "life"
    HEALTH = "health"
    COMMERCIAL = "commercial"
    UMBRELLA = "umbrella"


class PolicyStatus(StrEnum):
    ACTIVE = "active"
    LAPSED = "lapsed"
    CANCELLED = "cancelled"
    PENDING_RENEWAL = "pending_renewal"


class ClaimStatus(StrEnum):
    FNOL = "fnol"  # first notice of loss / just filed
    UNDER_REVIEW = "under_review"
    INFO_REQUESTED = "info_requested"
    APPROVED = "approved"
    DENIED = "denied"
    PAID = "paid"
    CLOSED = "closed"
    DISPUTED = "disputed"


class PaymentStatus(StrEnum):
    SCHEDULED = "scheduled"
    PENDING = "pending"
    PAID = "paid"
    FAILED = "failed"
    PAST_DUE = "past_due"
    REFUNDED = "refunded"


class BillingCadence(StrEnum):
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    SEMIANNUAL = "semiannual"
    ANNUAL = "annual"


class AppointmentType(StrEnum):
    AGENT_CALL = "agent_call"
    ADJUSTER_CALL = "adjuster_call"
    CLAIMS_CALL = "claims_call"


class AppointmentStatus(StrEnum):
    SCHEDULED = "scheduled"
    RESCHEDULED = "rescheduled"
    CANCELLED = "cancelled"
    COMPLETED = "completed"


class TicketStatus(StrEnum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"


class Urgency(StrEnum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


class AgentName(StrEnum):
    ORCHESTRATOR = "orchestrator"
    POLICY = "policy"
    CLAIMS = "claims"
    BILLING = "billing"
    ACCOUNT = "account"
    SCHEDULING = "scheduling"
    GENERAL = "general"
    ESCALATION = "escalation"


class VerificationStatus(StrEnum):
    UNVERIFIED = "unverified"
    IN_PROGRESS = "in_progress"
    VERIFIED = "verified"
    FAILED = "failed"


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"
