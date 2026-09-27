"""Chat endpoints: SSE streaming and conversation history (PROJECT.md Section 6).

Stream protocol, three named events:

* ``token`` - ``{"text": "..."}``, one per piece of answer as the model produces
  it. These arrive incrementally, which is the point of the endpoint.
* ``error`` - ``{"detail": "..."}``, emitted only when generation actually
  failed. It is sent *before* the final event so the final event keeps exactly
  the documented shape.
* ``done`` - ``{"answer", "has_sufficient_evidence", "citations"}``, always last,
  and always present even when generation failed.

A short-circuited answer (the evidence gate closed, so no model was consulted)
sends no ``token`` events - only ``done``. Those tokens would be fabricated: the
refusal text is decided by the gate, not generated. Emitting it as a stream would
imply otherwise. The final event still carries the full text.

Sessions: the conversation and the user question are committed *before* the
stream starts, so a generation failure still leaves a question in the history.
The assistant message and its citations are committed in a second short session
afterwards. Neither transaction is held open across the stream.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_session
from app.core.logging import get_logger
from app.db.session import session_scope
from app.rag.graph import stream_answer
from app.schemas.chat import (
    ChatFinal,
    ChatRequest,
    ConversationDetail,
    ConversationListResponse,
    ErrorEvent,
    TokenEvent,
)
from app.services import chat_service

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1", tags=["chat"])

#: Proxies and some browsers buffer small responses. Disabling buffering plus
#: these headers is what makes tokens actually arrive one at a time.
SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


def _sse(event: str, payload: dict) -> str:
    """One SSE frame. Payload is JSON on a single data line, per the spec."""
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def _open_conversation(
    conversation_id: uuid.UUID | None, query: str
) -> uuid.UUID:
    """Resolve the target conversation and store the question.

    Runs in its own committed session so the question is durable before any
    generation happens.
    """
    async with session_scope() as session:
        if conversation_id is None:
            conversation = await chat_service.create_conversation(session)
        else:
            conversation = await chat_service.get_conversation(session, conversation_id)
            if conversation is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"conversation {conversation_id} not found",
                )
        await chat_service.add_message(
            session, conversation.id, chat_service.ROLE_USER, query
        )
        return conversation.id


async def _close_conversation(conversation_id: uuid.UUID, state: dict) -> None:
    """Store the answer and its citations once generation is finished."""
    answer = state.get("answer") or ""
    citations = state.get("citations") or []
    async with session_scope() as session:
        message = await chat_service.add_message(
            session, conversation_id, chat_service.ROLE_ASSISTANT, answer
        )
        await chat_service.add_citations(session, message, citations)


@router.post(
    "/chat/stream",
    summary="Ask a question and stream the grounded answer with citations",
    response_class=StreamingResponse,
    responses={
        200: {"content": {"text/event-stream": {"schema": {"type": "string"}}}},
        404: {"description": "conversation_id does not exist"},
    },
)
async def chat_stream(
    payload: ChatRequest,
    request: Request,
) -> StreamingResponse:
    # No request-scoped session here on purpose: the stream needs its own short
    # transactions (see the module docstring) and holding one open across a
    # CPU-bound generation would idle a pooled connection for the whole answer.
    conversation_id = await _open_conversation(payload.conversation_id, payload.query)

    async def event_source() -> AsyncIterator[str]:
        final_state: dict | None = None
        try:
            async for piece, final in stream_answer(
                payload.query, document_ids=payload.document_ids
            ):
                if final is None:
                    yield _sse("token", TokenEvent(text=piece).model_dump())
                    continue
                final_state = final
        except Exception as exc:  # noqa: BLE001 - becomes an SSE error event
            logger.exception("chat stream failed")
            yield _sse("error", ErrorEvent(detail=str(exc)).model_dump())
            yield _sse(
                "done",
                ChatFinal(
                    answer=(
                        "I couldn't produce an answer for that question. "
                        "Nothing was generated."
                    ),
                    has_sufficient_evidence=False,
                    citations=[],
                ).model_dump(mode="json"),
            )
            return

        assert final_state is not None
        if await request.is_disconnected():
            logger.info("client disconnected mid-stream; dropping answer")
            return

        if final_state.get("error"):
            yield _sse("error", ErrorEvent(detail=str(final_state["error"])).model_dump())

        chat_final = ChatFinal(
            answer=final_state.get("answer") or "",
            has_sufficient_evidence=bool(final_state.get("has_sufficient_evidence")),
            citations=final_state.get("citations") or [],
        )

        # The stream is complete, so the answer is durable before it is announced.
        await _close_conversation(conversation_id, final_state)

        yield _sse("done", chat_final.model_dump(mode="json"))

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


@router.get(
    "/conversations",
    response_model=ConversationListResponse,
    summary="List conversations, most recently active first",
)
async def list_conversations(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> ConversationListResponse:
    return await chat_service.list_conversations(session, limit=limit, offset=offset)


@router.get(
    "/conversations/{conversation_id}",
    response_model=ConversationDetail,
    summary="Fetch one conversation with its messages and citations",
    responses={404: {"description": "No such conversation"}},
)
async def get_conversation(
    conversation_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
) -> ConversationDetail:
    detail = await chat_service.get_conversation_detail(session, conversation_id)
    if detail is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"conversation {conversation_id} not found",
        )
    return detail
