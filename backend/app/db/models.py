"""SQLAlchemy 2.0 async models.

Columns match PROJECT.md Section 5 exactly. Nothing is added that Section 5 does
not list, and nothing from the V2 sections (Section 13 ``document_versions``,
Section 14 ``query_logs`` / ``users.role``) is present - WORKFLOW.md Golden Rule 2.

Note on chunk text: Section 5 gives ``document_chunks`` no content column but
does give it ``qdrant_point_id``. Chunk text, ``heading_path`` and the full
metadata payload therefore live in the Qdrant point payload (PROJECT.md Section
4) and are joined back through ``qdrant_point_id``; that column is the intended
link. ``page_number`` is nullable because Docling emits no page provenance for
flow formats (DOCX/XLSX/CSV) - only paginated ones like PDF carry it.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.config import DocumentStatus
from app.db.base import Base


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )


class User(Base):
    """PROJECT.md Section 5: users — id, email, name."""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = _uuid_pk()
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)


class Document(Base):
    """PROJECT.md Section 5: documents — id, filename, file_type, storage_path,
    status, created_at."""

    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = _uuid_pk()
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    file_type: Mapped[str] = mapped_column(String(32), nullable=False)
    storage_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=DocumentStatus.UPLOADING.value
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    chunks: Mapped[list[DocumentChunk]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class DocumentChunk(Base):
    """PROJECT.md Section 5: document_chunks — id, document_id, chunk_index,
    page_number, section, qdrant_point_id."""

    __tablename__ = "document_chunks"
    __table_args__ = (
        Index("ix_document_chunks_document_id", "document_id"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    document_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    section: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    qdrant_point_id: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True
    )

    document: Mapped[Document] = relationship(back_populates="chunks")


class Conversation(Base):
    """PROJECT.md Section 5: conversations — id, user_id, title, created_at."""

    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    messages: Mapped[list[Message]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan"
    )


class Message(Base):
    """PROJECT.md Section 5: messages — id, conversation_id, role, content,
    created_at."""

    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = _uuid_pk()
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    conversation: Mapped[Conversation] = relationship(back_populates="messages")
    citations: Mapped[list[Citation]] = relationship(
        back_populates="message", cascade="all, delete-orphan"
    )


class Citation(Base):
    """PROJECT.md Section 5: citations — id, message_id, document_id,
    page_number, section, relevance_score.

    ``matched_text`` is an addition to that list, made in Stage 6 and recorded
    in migration 6c1d4a8b2f70. Section 5 does not list it, but Section 6 defines
    ``matched_text`` as part of the citation API object and requires it to be the
    literal substring used for verbatim highlighting. A stored conversation is
    replayed from these rows, so the excerpt has to survive the request. It is
    nullable so pre-migration rows and uncited answers both remain representable.
    """

    __tablename__ = "citations"

    id: Mapped[uuid.UUID] = _uuid_pk()
    message_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("messages.id", ondelete="CASCADE"),
        nullable=False,
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    section: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    relevance_score: Mapped[float] = mapped_column(Float, nullable=False)
    matched_text: Mapped[str | None] = mapped_column(Text, nullable=True)

    message: Mapped[Message] = relationship(back_populates="citations")
