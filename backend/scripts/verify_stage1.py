"""Stage 1 checkpoint: insert and read back one row per V1 table via the async ORM.

Exercises the SQLAlchemy 2.0 async models and engine against the real Postgres
container, not just the migration DDL. Cleans up after itself.

Usage: python -m scripts.verify_stage1
"""

from __future__ import annotations

import asyncio
import uuid

from sqlalchemy import select

from app.core.config import DocumentStatus
from app.core.eventloop import configure_event_loop
from app.db.models import (
    Citation,
    Conversation,
    Document,
    DocumentChunk,
    Message,
    User,
)
from app.db.session import dispose_engine, session_scope

FAILURES: list[str] = []


def check(label: str, got: object, expected: object) -> None:
    ok = got == expected
    if not ok:
        FAILURES.append(f"{label}: got {got!r}, expected {expected!r}")
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}: {got!r}")


async def main() -> int:
    run_id = uuid.uuid4()

    async with session_scope() as session:
        user = User(email=f"stage1-{run_id}@example.com", name="Stage One Verifier")
        session.add(user)
        await session.flush()

        document = Document(
            filename="stage1_probe.pdf",
            file_type="pdf",
            storage_path="backend/storage/stage1_probe.pdf",
            status=DocumentStatus.READY.value,
        )
        session.add(document)
        await session.flush()

        chunk = DocumentChunk(
            document_id=document.id,
            chunk_index=0,
            page_number=1,
            section="1. Purpose",
            qdrant_point_id=str(uuid.uuid4()),
        )
        session.add(chunk)

        conversation = Conversation(user_id=user.id, title="Stage 1 probe")
        session.add(conversation)
        await session.flush()

        message = Message(
            conversation_id=conversation.id, role="assistant", content="probe answer"
        )
        session.add(message)
        await session.flush()

        citation = Citation(
            message_id=message.id,
            document_id=document.id,
            page_number=1,
            section="1. Purpose",
            relevance_score=0.87,
        )
        session.add(citation)
        await session.flush()

        ids = {
            "user": user.id,
            "document": document.id,
            "chunk": chunk.id,
            "conversation": conversation.id,
            "message": message.id,
            "citation": citation.id,
        }

    # Re-read in a fresh transaction so this proves persistence, not session state.
    async with session_scope() as session:
        got_user = await session.scalar(
            select(User).where(User.id == ids["user"])
        )
        check("users.email", got_user.email if got_user else None,
              f"stage1-{run_id}@example.com")
        check("users.name", got_user.name if got_user else None, "Stage One Verifier")

        got_doc = await session.scalar(
            select(Document).where(Document.id == ids["document"])
        )
        check("documents.filename", got_doc.filename if got_doc else None,
              "stage1_probe.pdf")
        check("documents.status", got_doc.status if got_doc else None,
              DocumentStatus.READY.value)
        check("documents.created_at is set",
              got_doc.created_at is not None if got_doc else False, True)

        got_chunk = await session.scalar(
            select(DocumentChunk).where(DocumentChunk.id == ids["chunk"])
        )
        check("document_chunks.chunk_index",
              got_chunk.chunk_index if got_chunk else None, 0)
        check("document_chunks.page_number",
              got_chunk.page_number if got_chunk else None, 1)
        check("document_chunks.section",
              got_chunk.section if got_chunk else None, "1. Purpose")

        got_conv = await session.scalar(
            select(Conversation).where(Conversation.id == ids["conversation"])
        )
        check("conversations.title", got_conv.title if got_conv else None,
              "Stage 1 probe")
        check("conversations.user_id", got_conv.user_id if got_conv else None,
              ids["user"])

        got_msg = await session.scalar(
            select(Message).where(Message.id == ids["message"])
        )
        check("messages.role", got_msg.role if got_msg else None, "assistant")
        check("messages.content", got_msg.content if got_msg else None, "probe answer")

        got_cite = await session.scalar(
            select(Citation).where(Citation.id == ids["citation"])
        )
        check("citations.page_number",
              got_cite.page_number if got_cite else None, 1)
        check("citations.relevance_score",
              round(got_cite.relevance_score, 2) if got_cite else None, 0.87)
        check("citations.document_id", got_cite.document_id if got_cite else None,
              ids["document"])

    # Cleanup, children first (FK cascade would handle it, but be explicit).
    async with session_scope() as session:
        for model, key in (
            (Citation, "citation"),
            (Message, "message"),
            (Conversation, "conversation"),
            (DocumentChunk, "chunk"),
            (Document, "document"),
            (User, "user"),
        ):
            await session.execute(
                select(model).where(model.id == ids[key]).with_for_update()
            )
            row = await session.scalar(select(model).where(model.id == ids[key]))
            if row is not None:
                await session.delete(row)
        await session.flush()

    await dispose_engine()

    print()
    if FAILURES:
        print(f"STAGE 1 FAILED ({len(FAILURES)}):")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print("STAGE 1 PASS: all 6 tables insert + read back cleanly (async ORM).")
    return 0


if __name__ == "__main__":
    configure_event_loop()
    raise SystemExit(asyncio.run(main()))
