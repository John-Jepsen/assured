"""SQLAlchemy ORM models for the insurance domain and platform state.

All data is SYNTHETIC. No real PII is stored. Embeddings are kept as a JSON float
array so the schema is portable across SQLite / Postgres / pgvector; the pgvector
backend additionally maintains a native ``vector`` column (see migrations).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from insurance_ai.db.base import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    first_name: Mapped[str] = mapped_column(String)
    last_name: Mapped[str] = mapped_column(String)
    email: Mapped[str] = mapped_column(String, unique=True)
    phone: Mapped[str] = mapped_column(String)
    date_of_birth: Mapped[date] = mapped_column(Date)
    zip_code: Mapped[str] = mapped_column(String)
    address: Mapped[str] = mapped_column(String)
    comm_preference: Mapped[str] = mapped_column(String, default="email")
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    policies: Mapped[list["Policy"]] = relationship(back_populates="customer")
    claims: Mapped[list["Claim"]] = relationship(back_populates="customer")


class Policy(Base):
    __tablename__ = "policies"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    policy_number: Mapped[str] = mapped_column(String, unique=True, index=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id"), index=True)
    product_type: Mapped[str] = mapped_column(String, index=True)
    status: Mapped[str] = mapped_column(String)
    effective_date: Mapped[date] = mapped_column(Date)
    renewal_date: Mapped[date] = mapped_column(Date)
    premium_amount: Mapped[float] = mapped_column(Numeric(10, 2))
    billing_cadence: Mapped[str] = mapped_column(String, default="monthly")
    autopay: Mapped[bool] = mapped_column(Boolean, default=False)
    # Product-specific structured detail (drivers, dependents, dwelling, etc.)
    details: Mapped[dict] = mapped_column(JSON, default=dict)

    customer: Mapped[Customer] = relationship(back_populates="policies")
    assets: Mapped[list["InsuredAsset"]] = relationship(back_populates="policy")
    coverages: Mapped[list["Coverage"]] = relationship(back_populates="policy")
    claims: Mapped[list["Claim"]] = relationship(back_populates="policy")
    invoices: Mapped[list["Invoice"]] = relationship(back_populates="policy")


class InsuredAsset(Base):
    __tablename__ = "insured_assets"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    policy_id: Mapped[str] = mapped_column(ForeignKey("policies.id"), index=True)
    asset_type: Mapped[str] = mapped_column(String)  # vehicle, dwelling, life, etc.
    description: Mapped[str] = mapped_column(String)
    identifier: Mapped[str | None] = mapped_column(String, nullable=True)  # VIN / address
    attributes: Mapped[dict] = mapped_column(JSON, default=dict)

    policy: Mapped[Policy] = relationship(back_populates="assets")


class Coverage(Base):
    __tablename__ = "coverages"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    policy_id: Mapped[str] = mapped_column(ForeignKey("policies.id"), index=True)
    coverage_type: Mapped[str] = mapped_column(String)  # collision, liability, dwelling...
    limit_amount: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    deductible: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    per_unit: Mapped[str | None] = mapped_column(String, nullable=True)  # per day/incident
    exclusions: Mapped[list] = mapped_column(JSON, default=list)
    description: Mapped[str] = mapped_column(Text, default="")

    policy: Mapped[Policy] = relationship(back_populates="coverages")


class Claim(Base):
    __tablename__ = "claims"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    claim_number: Mapped[str] = mapped_column(String, unique=True, index=True)
    policy_id: Mapped[str] = mapped_column(ForeignKey("policies.id"), index=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id"), index=True)
    status: Mapped[str] = mapped_column(String)
    loss_type: Mapped[str] = mapped_column(String)
    description: Mapped[str] = mapped_column(Text)
    date_of_loss: Mapped[date] = mapped_column(Date)
    reported_date: Mapped[date] = mapped_column(Date, default=lambda: _now().date())
    adjuster_name: Mapped[str | None] = mapped_column(String, nullable=True)
    adjuster_phone: Mapped[str | None] = mapped_column(String, nullable=True)
    reserve_amount: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    next_steps: Mapped[list] = mapped_column(JSON, default=list)
    requested_documents: Mapped[list] = mapped_column(JSON, default=list)
    notes: Mapped[list] = mapped_column(JSON, default=list)

    customer: Mapped[Customer] = relationship(back_populates="claims")
    policy: Mapped[Policy] = relationship(back_populates="claims")


class Invoice(Base):
    __tablename__ = "invoices"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    invoice_number: Mapped[str] = mapped_column(String, unique=True, index=True)
    policy_id: Mapped[str] = mapped_column(ForeignKey("policies.id"), index=True)
    amount_due: Mapped[float] = mapped_column(Numeric(10, 2))
    amount_paid: Mapped[float] = mapped_column(Numeric(10, 2), default=0)
    due_date: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String)
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)

    policy: Mapped[Policy] = relationship(back_populates="invoices")
    payments: Mapped[list["Payment"]] = relationship(back_populates="invoice")


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    invoice_id: Mapped[str] = mapped_column(ForeignKey("invoices.id"), index=True)
    amount: Mapped[float] = mapped_column(Numeric(10, 2))
    status: Mapped[str] = mapped_column(String)
    method: Mapped[str] = mapped_column(String, default="mock")  # mock / stripe_test
    provider_reference: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    invoice: Mapped[Invoice] = relationship(back_populates="payments")


class Appointment(Base):
    __tablename__ = "appointments"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id"), index=True)
    policy_id: Mapped[str | None] = mapped_column(ForeignKey("policies.id"), nullable=True)
    claim_id: Mapped[str | None] = mapped_column(ForeignKey("claims.id"), nullable=True)
    appointment_type: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="scheduled")
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class SupportTicket(Base):
    __tablename__ = "support_tickets"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    ticket_number: Mapped[str] = mapped_column(String, unique=True, index=True)
    customer_id: Mapped[str | None] = mapped_column(ForeignKey("customers.id"), nullable=True)
    conversation_id: Mapped[str | None] = mapped_column(
        ForeignKey("conversations.id"), nullable=True
    )
    status: Mapped[str] = mapped_column(String, default="open")
    urgency: Mapped[str] = mapped_column(String, default="normal")
    reason: Mapped[str] = mapped_column(Text)
    summary: Mapped[str] = mapped_column(Text)
    handoff: Mapped[dict] = mapped_column(JSON, default=dict)  # structured handoff payload
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    customer_id: Mapped[str | None] = mapped_column(ForeignKey("customers.id"), nullable=True)
    channel: Mapped[str] = mapped_column(String, default="web")  # web / phone
    verification_status: Mapped[str] = mapped_column(String, default="unverified")
    current_agent: Mapped[str] = mapped_column(String, default="orchestrator")
    escalated: Mapped[bool] = mapped_column(Boolean, default=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )

    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation", order_by="Message.created_at"
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    role: Mapped[str] = mapped_column(String)
    content: Mapped[str] = mapped_column(Text)
    agent: Mapped[str | None] = mapped_column(String, nullable=True)
    intent: Mapped[str | None] = mapped_column(String, nullable=True)
    # Structured execution trace (tool calls, sources, latencies) — NOT chain-of-thought.
    trace: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


class ToolExecution(Base):
    __tablename__ = "tool_executions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    conversation_id: Mapped[str | None] = mapped_column(
        ForeignKey("conversations.id"), nullable=True
    )
    tool_name: Mapped[str] = mapped_column(String, index=True)
    agent: Mapped[str | None] = mapped_column(String, nullable=True)
    arguments: Mapped[dict] = mapped_column(JSON, default=dict)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    error_code: Mapped[str | None] = mapped_column(String, nullable=True)
    latency_ms: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class KnowledgeDocument(Base):
    __tablename__ = "knowledge_documents"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    source_path: Mapped[str] = mapped_column(String, unique=True)
    title: Mapped[str] = mapped_column(String)
    product_type: Mapped[str | None] = mapped_column(String, index=True, nullable=True)
    category: Mapped[str] = mapped_column(String)  # billing, claims, faq, ...
    content: Mapped[str] = mapped_column(Text)

    chunks: Mapped[list["DocumentChunk"]] = relationship(back_populates="document")


class DocumentChunk(Base):
    __tablename__ = "document_chunks"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    document_id: Mapped[str] = mapped_column(ForeignKey("knowledge_documents.id"), index=True)
    chunk_index: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    citation: Mapped[str] = mapped_column(String)  # human-readable source label
    product_type: Mapped[str | None] = mapped_column(String, index=True, nullable=True)
    category: Mapped[str] = mapped_column(String, index=True)
    # Portable embedding storage (float array as JSON). pgvector backend mirrors
    # this into a native vector column via migration for ANN search.
    embedding: Mapped[list] = mapped_column(JSON, default=list)

    document: Mapped[KnowledgeDocument] = relationship(back_populates="chunks")


class EvaluationRun(Base):
    __tablename__ = "evaluation_runs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    suite: Mapped[str] = mapped_column(String)
    total: Mapped[int] = mapped_column(Integer)
    passed: Mapped[int] = mapped_column(Integer)
    results: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
