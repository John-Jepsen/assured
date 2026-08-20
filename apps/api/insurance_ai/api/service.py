"""Conversation service — ties orchestrator to persistence + session state.

Responsibilities: create/load a conversation, keep the in-memory Session in sync
with the persisted verification state, run the orchestrator, persist the user and
assistant messages with their execution trace, and record ToolExecution rows.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from insurance_ai.agents.orchestrator import Orchestrator, OrchestratorResult
from insurance_ai.db.models import Conversation, Message, ToolExecution
from insurance_ai.domain.enums import MessageRole, VerificationStatus
from insurance_ai.security.session import Session, get_session_store
from insurance_ai.observability import get_logger

log = get_logger("service")


@dataclass
class TurnResult:
    conversation_id: str
    answer: str
    trace: dict[str, Any]
    needs_verification: bool


class ConversationService:
    def __init__(self, orchestrator: Orchestrator | None = None) -> None:
        self.orchestrator = orchestrator or Orchestrator()
        self.sessions = get_session_store()

    async def get_or_create_conversation(
        self, db: AsyncSession, conversation_id: str | None, *, channel: str = "web",
        customer_id: str | None = None,
    ) -> Conversation:
        if conversation_id:
            conv = await db.get(Conversation, conversation_id)
            if conv:
                return conv
        conv = Conversation(channel=channel, customer_id=customer_id)
        db.add(conv)
        await db.commit()
        await db.refresh(conv)
        return conv

    def _sync_session(self, conv: Conversation) -> Session:
        sess = self.sessions.get(conv.id)
        if conv.customer_id and not sess.customer_id:
            sess.customer_id = conv.customer_id
        return sess

    async def handle_message(
        self, db: AsyncSession, conversation_id: str | None, message: str,
        *, channel: str = "web", request_id: str = "req",
    ) -> tuple[OrchestratorResult, Session, Conversation]:
        conv = await self.get_or_create_conversation(db, conversation_id, channel=channel)
        sess = self._sync_session(conv)

        # persist user message
        db.add(Message(conversation_id=conv.id, role=MessageRole.USER, content=message))
        await db.commit()

        async def sink(record: dict[str, Any]) -> None:
            db.add(ToolExecution(
                conversation_id=conv.id, tool_name=record["tool_name"],
                arguments=record.get("arguments", {}), result=record.get("result", {}),
                ok=record.get("ok", True), error_code=record.get("error_code"),
                latency_ms=record.get("latency_ms", 0.0),
            ))

        result = await self.orchestrator.run(
            db, sess, message, request_id=request_id, log_sink=sink
        )

        # sync persisted conversation state from session
        conv.verification_status = sess.verification_status
        conv.current_agent = result.trace.agents[0] if result.trace.agents else "orchestrator"
        conv.escalated = sess.escalated or conv.escalated
        conv.customer_id = sess.customer_id or conv.customer_id
        db.add(Message(
            conversation_id=conv.id, role=MessageRole.ASSISTANT, content=result.answer,
            agent=conv.current_agent, intent=",".join(result.trace.intents),
            trace=result.trace.as_dict(),
        ))
        await db.commit()
        return result, sess, conv

    async def transcript(self, db: AsyncSession, conversation_id: str) -> list[dict[str, Any]]:
        rows = await db.execute(
            select(Message).where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at)
        )
        return [
            {"role": m.role, "content": m.content, "agent": m.agent, "intent": m.intent,
             "trace": m.trace, "created_at": m.created_at.isoformat()}
            for m in rows.scalars().all()
        ]
