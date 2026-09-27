"""Chat and conversation API models (PROJECT.md Section 6).

The citation shape here is the contract: ``id``, ``document_id``,
``document_name``, ``page``, ``section``, ``matched_text`` and ``relevance``.
``matched_text`` is a verbatim substring of the retrieved chunk, not a
paraphrase, because the frontend highlights it in the source document.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class Citation(BaseModel):
    """One supporting chunk for an answer."""

    id: str = Field(description="Stable within one answer, e.g. 'c1'")
    document_id: uuid.UUID
    document_name: str
    page: int | None = Field(default=None, description="1-based; null for flow formats")
    section: str | None = None
    matched_text: str = Field(
        description="Verbatim substring of the cited chunk, used for highlighting"
    )
    relevance: float = Field(
        description="Cross-encoder relevance, sigmoid of the raw logit, in [0, 1]"
    )


class ChatRequest(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    conversation_id: uuid.UUID | None = Field(
        default=None,
        description="Continue this conversation. Omit to start a new one.",
    )
    document_ids: list[uuid.UUID] | None = Field(
        default=None,
        description="Restrict retrieval to these documents. Omit to search everything.",
    )


class TokenEvent(BaseModel):
    """One SSE `token` event: an incremental piece of the answer."""

    text: str


class ChatFinal(BaseModel):
    """The final SSE event. Exactly this shape, per PROJECT.md Section 6."""

    answer: str
    has_sufficient_evidence: bool
    citations: list[Citation]


class ErrorEvent(BaseModel):
    """One SSE `error` event. Generation failures surface here rather than
    being reported as a short or empty answer."""

    detail: str


class ConversationSummary(BaseModel):
    id: uuid.UUID
    title: str
    created_at: datetime
    message_count: int
    last_message_at: datetime | None = Field(
        default=None,
        description=(
            "Max created_at over the conversation's messages. Section 5 gives "
            "conversations no updated_at, so recency is derived from messages."
        ),
    )


class MessageOut(BaseModel):
    id: uuid.UUID
    role: str
    content: str
    created_at: datetime
    citations: list[Citation] = Field(default_factory=list)


class ConversationListResponse(BaseModel):
    conversations: list[ConversationSummary]
    total: int


class ConversationDetail(ConversationSummary):
    messages: list[MessageOut] = Field(
        default_factory=list, description="Oldest first, with citations on answers"
    )
