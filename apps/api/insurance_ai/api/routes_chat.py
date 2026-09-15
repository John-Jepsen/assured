"""Chat: REST (non-streaming) + WebSocket (token streaming) + explicit verify."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from insurance_ai.api.schemas import ChatRequest, ChatResponse, ChatSocketRequest, VerifyRequest
from insurance_ai.api.service import ConversationService
from insurance_ai.db.base import SessionFactory, get_session
from insurance_ai.observability import get_logger
from insurance_ai.security.session import get_session_store
from insurance_ai.security.verification import VerificationClaim, verify_identity

router = APIRouter(prefix="/api", tags=["chat"])
log = get_logger("chat")
_service = ConversationService()


@router.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest, db: AsyncSession = Depends(get_session)) -> ChatResponse:
    result, _sess, conv = await _service.handle_message(
        db, req.conversation_id, req.message, channel=req.channel, request_id=str(uuid.uuid4())
    )
    return ChatResponse(
        conversation_id=conv.id,
        answer=result.answer,
        needs_verification=result.needs_verification,
        trace=result.trace.as_dict(),
    )


@router.post("/verify")
async def verify(req: VerifyRequest, db: AsyncSession = Depends(get_session)) -> dict:
    """Deterministic verification endpoint (never routed through the LLM)."""
    sess = get_session_store().get(req.conversation_id)
    result = await verify_identity(
        db,
        sess,
        VerificationClaim(
            last_name=req.last_name,
            zip_code=req.zip_code,
            policy_number=req.policy_number,
            date_of_birth=req.date_of_birth,
            otp_code=req.otp_code,
        ),
    )
    from insurance_ai.db.models import Conversation

    conv = await db.get(Conversation, req.conversation_id)
    if conv:
        conv.verification_status = sess.verification_status
        conv.customer_id = sess.customer_id or conv.customer_id
        await db.commit()
    return {
        "status": result.status,
        "verified": sess.is_verified,
        "matched_factors": result.matched_factors,
        "message": result.message,
        "attempts_remaining": result.attempts_remaining,
    }


@router.websocket("/ws/chat")
async def ws_chat(ws: WebSocket) -> None:
    """Streaming chat: client sends {message, conversation_id}; server streams tokens."""
    await ws.accept()
    try:
        while True:
            payload = await ws.receive_json()
            try:
                request = ChatSocketRequest.model_validate(payload)
            except ValidationError:
                await ws.send_json(
                    {
                        "type": "error",
                        "code": "invalid_request",
                        "message": "The chat message is empty, too large, or malformed.",
                    }
                )
                continue
            message = request.message
            conversation_id = request.conversation_id
            async with SessionFactory() as db:
                turn = await _service.prepare_message(
                    db, conversation_id, message, request_id=str(uuid.uuid4())
                )
                result = turn.result
                conv = turn.conversation
                await ws.send_json(
                    {
                        "type": "meta",
                        "conversation_id": conv.id,
                        "trace": result.trace.as_dict(),
                        "needs_verification": result.needs_verification,
                    }
                )
                rendered: list[str] = []
                async for token in _service.orchestrator.stream_result(result, message):
                    rendered.append(token)
                    await ws.send_json({"type": "token", "token": token})
                result, _sess, conv = await _service.finish_message(
                    db, turn, "".join(rendered)
                )
                await ws.send_json(
                    {"type": "done", "answer": result.answer, "sources": result.trace.sources}
                )
    except WebSocketDisconnect:
        log.info("ws_chat_disconnect")
    except Exception as e:
        log.error("ws_chat_error", error=type(e).__name__)
        try:
            await ws.send_json({"type": "error", "message": "The chat stream failed."})
        except Exception:
            pass
