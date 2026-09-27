"""Document service: storage, rows, and ingestion kick-off.

Route handlers call these; they contain no FastAPI concerns
(WORKFLOW.md Section 4).
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

from fastapi import UploadFile
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import (
    FILE_TYPE_BY_EXTENSION,
    IN_PROGRESS_STATUSES,
    DocumentStatus,
    get_settings,
)
from app.core.logging import get_logger
from app.db.models import Document, DocumentChunk
from app.db.session import session_scope
from app.ingestion.pipeline import ingest_document
from app.services.chunk_purge import purge_document_chunks

logger = get_logger(__name__)

#: Ingestion runs inline in a background task (PROJECT.md Section 2: no Celery /
#: worker queue in V1), so the upload response can return immediately and the
#: sidebar can watch real stage transitions.
_background_tasks: set[asyncio.Task[None]] = set()


def storage_root() -> Path:
    root = get_settings().storage_dir
    root.mkdir(parents=True, exist_ok=True)
    return root


def resolve_file_type(filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    file_type = FILE_TYPE_BY_EXTENSION.get(suffix)
    if file_type is None:
        raise ValueError(f"unsupported file type: {suffix}")
    return file_type


async def save_upload(upload: UploadFile, document_id: uuid.UUID) -> Path:
    """Stream the upload to local disk. PROJECT.md Section 2: no S3/MinIO in V1."""
    settings = get_settings()
    suffix = Path(upload.filename or "").suffix.lower()
    destination = storage_root() / f"{document_id}{suffix}"

    written = 0
    with destination.open("wb") as handle:
        while chunk := await upload.read(1024 * 1024):
            written += len(chunk)
            if written > settings.max_upload_bytes:
                handle.close()
                destination.unlink(missing_ok=True)
                raise ValueError(
                    f"file exceeds {settings.max_upload_bytes} byte limit"
                )
            # Blocking disk write, kept off the event loop.
            await asyncio.to_thread(handle.write, chunk)

    if written == 0:
        destination.unlink(missing_ok=True)
        raise ValueError("uploaded file is empty")

    logger.info("stored upload %s (%d bytes)", destination.name, written)
    return destination


async def create_document(
    session: AsyncSession, filename: str, file_type: str, storage_path: Path
) -> Document:
    document = Document(
        filename=filename,
        file_type=file_type,
        storage_path=str(storage_path),
        status=DocumentStatus.UPLOADING.value,
    )
    session.add(document)
    await session.flush()
    return document


def start_ingestion(document_id: uuid.UUID, path: Path) -> None:
    """Fire-and-forget ingestion, holding a strong reference to the task."""
    task = asyncio.create_task(_run_ingestion(document_id, path))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


async def _run_ingestion(document_id: uuid.UUID, path: Path) -> None:
    try:
        await ingest_document(document_id, path)
    except Exception:  # noqa: BLE001 - already logged + status=failed in pipeline
        logger.error("background ingestion for %s ended in failure", document_id)


async def reconcile_interrupted_documents() -> int:
    """Fail documents left mid-pipeline by a crash or restart, and drop their chunks.

    Ingestion is an in-process task (no worker queue in V1), so a hard shutdown
    can strand a row in a non-terminal stage forever. At boot any such row is
    marked ``failed`` so the sidebar shows a terminal state instead of spinning.

    The chunk points are deleted too, and this is not merely tidy-up. When the
    process dies, ``ingest_document``'s own failure handler never runs - it
    catches ``Exception``, and a SIGKILL or OOM kill runs no handler at all - so
    any points already upserted would stay in Qdrant indefinitely. Retrieval does
    not filter on status, so they would keep being retrieved and cited as
    evidence for a document the user was told had failed.

    Scope note: this sweeps documents in a *non-terminal* stage only. Documents
    already marked ``failed`` were cleaned by the in-process handler, and
    re-filtering every failed document on every boot would mean a Qdrant scan for
    no additional safety.
    """
    staged = [s.value for s in IN_PROGRESS_STATUSES]
    async with session_scope() as session:
        rows = (
            await session.execute(
                select(Document).where(Document.status.in_(staged))
            )
        ).scalars().all()
        interrupted = [(document.id, document.status, document.filename) for document in rows]

    for document_id, status, filename in interrupted:
        logger.warning(
            "document %s (%s) interrupted in status=%s; purging chunks and marking failed",
            document_id,
            filename,
            status,
        )
        # Vectors first, so the document is never left observably failed while
        # its chunks are still retrievable.
        await purge_document_chunks(document_id)

    if interrupted:
        async with session_scope() as session:
            await session.execute(
                update(Document)
                .where(Document.id.in_([document_id for document_id, _, _ in interrupted]))
                .values(status=DocumentStatus.FAILED.value)
            )
        logger.info("reconciled %d interrupted document(s)", len(interrupted))
    return len(interrupted)


async def list_documents(session: AsyncSession) -> list[tuple[Document, int]]:
    stmt = (
        select(Document, func.count(DocumentChunk.id))
        .outerjoin(DocumentChunk, DocumentChunk.document_id == Document.id)
        .group_by(Document.id)
        .order_by(Document.created_at.desc())
    )
    rows = await session.execute(stmt)
    return [(document, count) for document, count in rows.all()]


async def get_document(
    session: AsyncSession, document_id: uuid.UUID
) -> tuple[Document, int] | None:
    stmt = (
        select(Document, func.count(DocumentChunk.id))
        .outerjoin(DocumentChunk, DocumentChunk.document_id == Document.id)
        .where(Document.id == document_id)
        .group_by(Document.id)
    )
    row = (await session.execute(stmt)).one_or_none()
    return (row[0], row[1]) if row else None
