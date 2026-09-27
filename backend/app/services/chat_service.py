"""Conversation, message and citation persistence (Stage 6).

V1 is single-tenant: PROJECT.md Section 2 states there is one set of users and no
per-department permissions, and the auth schema is V2 (Section 14). But
``conversations.user_id`` is ``NOT NULL``, so something has to own each
conversation. Rather than fake an auth system, one configured user row is created
on first use and every conversation belongs to it. That keeps the foreign key
honest and leaves the auth work where the spec puts it.

Streaming complicates persistence: the assistant message is written *after* the
stream finishes, because the answer text does not exist until then. The
conversation is created and the user message stored up front, so a conversation
that fails mid-generation still shows what was asked.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.models import Citation, Conversation, Message, User
from app.schemas.chat import (
    Citation as CitationSchema,
    ConversationDetail,
    ConversationListResponse,
    ConversationSummary,
    MessageOut,
)

logger = get_logger(__name__)

ROLE_USER = "user"
ROLE_ASSISTANT = "assistant"

#: Fallback conversation title. PROJECT.md Section 5 requires a title but says
#: nothing about how it is chosen, and V1 has no summariser available offline.
DEFAULT_TITLE = "New chat"

#: Conversations carry no updated_at in Section 5, so listing order is derived
#: from the newest message in each conversation.
_LAST_MESSAGE = func.max(Message.created_at)


async def get_or_create_user(session: AsyncSession) -> User:
    """The single owning user for V1, created on first use."""
    settings = get_settings()
    user = await session.scalar(
        select(User).where(User.email == settings.default_user_email)
    )
    if user is not None:
        return user

    user = User(email=settings.default_user_email, name=settings.default_user_name)
    session.add(user)
    await session.flush()
    logger.info("created V1 default user %s (%s)", user.email, user.id)
    return user


async def create_conversation(session: AsyncSession, title: str | None = None) -> Conversation:
    user = await get_or_create_user(session)
    conversation = Conversation(user_id=user.id, title=title or DEFAULT_TITLE)
    session.add(conversation)
    await session.flush()
    logger.info("created conversation %s", conversation.id)
    return conversation


async def get_conversation(session: AsyncSession, conversation_id: uuid.UUID) -> Conversation | None:
    return await session.scalar(
        select(Conversation).where(Conversation.id == conversation_id)
    )


async def add_message(
    session: AsyncSession, conversation_id: uuid.UUID, role: str, content: str
) -> Message:
    message = Message(conversation_id=conversation_id, role=role, content=content)
    session.add(message)
    await session.flush()
    return message


async def add_citations(
    session: AsyncSession,
    message: Message,
    citations: list[CitationSchema],
) -> list[Citation]:
    """Persist the citations attached to an assistant message.

    ``relevance`` from the API becomes ``relevance_score`` in Section 5's column
    naming. ``matched_text`` is the verbatim excerpt the frontend highlights.
    """
    rows: list[Citation] = []
    for citation in citations:
        row = Citation(
            message_id=message.id,
            document_id=citation.document_id,
            page_number=citation.page,
            section=citation.section,
            relevance_score=citation.relevance,
            matched_text=citation.matched_text,
        )
        session.add(row)
        rows.append(row)
    if rows:
        logger.info("persisted %d citation(s) on message %s", len(rows), message.id)
    return rows


async def list_conversations(
    session: AsyncSession, limit: int = 50, offset: int = 0
) -> ConversationListResponse:
    """Conversations newest-activity-first, with per-conversation counts."""
    counts = (
        select(
            Message.conversation_id.label("conversation_id"),
            func.count(Message.id).label("message_count"),
            _LAST_MESSAGE.label("last_message_at"),
        )
        .group_by(Message.conversation_id)
        .subquery()
    )

    rows = (
        await session.execute(
            select(Conversation, counts.c.message_count, counts.c.last_message_at)
            .outerjoin(counts, counts.c.conversation_id == Conversation.id)
            .order_by(counts.c.last_message_at.desc().nullslast(), Conversation.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
    ).all()

    total = await session.scalar(
        select(func.count(Conversation.id))
    )

    return ConversationListResponse(
        total=int(total or 0),
        conversations=[
            ConversationSummary(
                id=conversation.id,
                title=conversation.title,
                created_at=conversation.created_at,
                message_count=int(message_count or 0),
                last_message_at=last_message_at,
            )
            for conversation, message_count, last_message_at in rows
        ],
    )


async def _document_names(session: AsyncSession, document_ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
    """Filenames for citations, since Section 5 stores only document_id."""
    if not document_ids:
        return {}
    from app.db.models import Document

    rows = await session.execute(
        select(Document.id, Document.filename).where(Document.id.in_(document_ids))
    )
    return {document_id: filename for document_id, filename in rows.all()}


def _citation_schema(
    row: Citation, names: dict[uuid.UUID, str], index: int
) -> CitationSchema:
    return CitationSchema(
        id=f"c{index}",
        document_id=row.document_id,
        document_name=names.get(row.document_id, ""),
        page=row.page_number,
        section=row.section,
        matched_text=row.matched_text or "",
        relevance=row.relevance_score,
    )


async def get_conversation_detail(
    session: AsyncSession, conversation_id: uuid.UUID
) -> ConversationDetail | None:
    """A conversation with all its messages and citations, oldest first."""
    conversation = await get_conversation(session, conversation_id)
    if conversation is None:
        return None

    messages = (
        await session.scalars(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at.asc(), Message.id.asc())
        )
    ).all()

    citation_rows = (
        await session.scalars(
            select(Citation)
            .where(Citation.message_id.in_([m.id for m in messages]))
            .order_by(Citation.id.asc())
        )
    ).all()

    by_message: dict[uuid.UUID, list[Citation]] = {}
    for row in citation_rows:
        by_message.setdefault(row.message_id, []).append(row)

    names = await _document_names(
        session, {row.document_id for row in citation_rows}
    )

    return ConversationDetail(
        id=conversation.id,
        title=conversation.title,
        created_at=conversation.created_at,
        message_count=len(messages),
        last_message_at=max((m.created_at for m in messages), default=None),
        messages=[
            MessageOut(
                id=message.id,
                role=message.role,
                content=message.content,
                created_at=message.created_at,
                citations=[
                    _citation_schema(row, names, index)
                    for index, row in enumerate(by_message.get(message.id, []), start=1)
                ],
            )
            for message in messages
        ],
    )
