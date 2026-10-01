"""Ingestion pipeline: parse -> chunk -> embed -> index -> ready.

Each stage commits its own status transition so the sidebar in PROJECT.md
Section 11.2 observes genuine intermediate states (Parsing, Chunking, Embedding,
Indexing) rather than a single jump from uploading to ready.

Failures set status to ``failed``, delete any chunk points already written to
Qdrant, and re-raise - WORKFLOW.md Golden Rule 10 forbids swallowing ingestion
errors. The deletion matters for correctness, not tidiness: retrieval does not
filter on document status, so a point belonging to a document that never reached
``ready`` would still be returned, reranked and cited as evidence, breaking the
citation-trust promise in PROJECT.md Section 1.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from pathlib import Path

from qdrant_client.http.models import PointStruct
from sqlalchemy import select

from app.core.config import DocumentStatus, get_settings
from app.core.logging import get_logger
from app.db.models import Document, DocumentChunk
from app.db.session import session_scope
from app.ingestion.chunker import chunk_document
from app.ingestion.parser import extract_document_pages, parse_document
from app.services import bm25
from app.services.chunk_purge import purge_document_chunks
from app.services.corpus_stats import corpus_stats
from app.services.embeddings import dense_dimension, embed_documents
from app.services.qdrant import DENSE_VECTOR, SPARSE_VECTOR, ensure_collection, get_qdrant_client

logger = get_logger(__name__)


async def set_status(document_id: uuid.UUID, status: DocumentStatus) -> None:
    """Commit a status transition on its own so observers can see each stage."""
    async with session_scope() as session:
        document = await session.scalar(
            select(Document).where(Document.id == document_id)
        )
        if document is None:
            raise RuntimeError(f"document {document_id} vanished mid-ingestion")
        previous = document.status
        document.status = status.value
        logger.info(
            "document %s: %s -> %s", document_id, previous, status.value
        )


async def _purge_then_mark_failed(document_id: uuid.UUID, reason: str) -> None:
    """Discard partial chunks, then persist ``failed``.

    Ordering is deliberate: the vectors go first, so the document is never
    observably ``failed`` while its chunks are still retrievable. A document
    already marked ``ready`` is left completely alone - a late error after the
    terminal transition must never delete a good document's evidence.
    """
    async with session_scope() as session:
        current = await session.scalar(
            select(Document.status).where(Document.id == document_id)
        )
    if current is None:
        raise RuntimeError(f"document {document_id} vanished mid-ingestion")
    if current == DocumentStatus.READY.value:
        logger.warning(
            "document %s already reached ready; leaving its chunks intact (%s)",
            document_id,
            reason,
        )
        return

    # A failed purge must not stop the status write: the document has to reach a
    # terminal state whatever happens, or the sidebar spins forever. Boot
    # reconciliation is the backstop for any vectors this leaves behind.
    try:
        await purge_document_chunks(document_id)
        cache_file = get_settings().storage_dir / f"{document_id}.pages.json"
        cache_file.unlink(missing_ok=True)
    except Exception:  # noqa: BLE001 - already failing; keep the original cause
        logger.exception("could not purge partial chunks for %s", document_id)
    await set_status(document_id, DocumentStatus.FAILED)


async def _mark_failed_best_effort(document_id: uuid.UUID, reason: str) -> None:
    """Persist ``failed`` and clean up even while the task is being cancelled.

    Once a task is cancelled, every subsequent ``await`` re-raises
    ``CancelledError`` immediately, so the work is handed to a shielded task
    that is allowed to finish on its own.
    """
    writer = asyncio.create_task(_purge_then_mark_failed(document_id, reason))
    try:
        await asyncio.shield(writer)
    except asyncio.CancelledError:
        # Cancellation targeted us; the shielded writer keeps running.
        pass
    except Exception:  # noqa: BLE001 - already failing; keep original cause
        logger.exception("could not clean up document %s", document_id)
    else:
        logger.info("document %s: marked failed (%s)", document_id, reason)


async def ingest_document(document_id: uuid.UUID, path: Path) -> int:
    """Run the full pipeline for one stored file. Returns the chunk count."""
    started = time.perf_counter()
    logger.info("document %s: ingestion started (%s)", document_id, path.name)

    try:
        await set_status(document_id, DocumentStatus.PARSING)
        stage = time.perf_counter()
        parsed = await parse_document(path)
        logger.info("document %s: parsed in %.2fs", document_id, time.perf_counter() - stage)

        # Cache page text + bounding boxes for sub-10ms evidence viewer response (PROJECT.md Section 6).
        try:
            pages_data = extract_document_pages(parsed)
            cache_file = get_settings().storage_dir / f"{document_id}.pages.json"
            await asyncio.to_thread(cache_file.write_text, json.dumps(pages_data), encoding="utf-8")
        except Exception:
            logger.exception("document %s: could not cache pages JSON", document_id)

        await set_status(document_id, DocumentStatus.CHUNKING)
        stage = time.perf_counter()
        chunks = chunk_document(parsed)
        if not chunks:
            raise RuntimeError(f"no content extracted from {path.name}")
        table_count = sum(1 for c in chunks if c.content_type == "table")
        logger.info(
            "document %s: %d chunk(s) (%d table) in %.2fs",
            document_id,
            len(chunks),
            table_count,
            time.perf_counter() - stage,
        )

        await set_status(document_id, DocumentStatus.EMBEDDING)
        stage = time.perf_counter()

        collection = await ensure_collection(dense_dimension())

        dense_vectors = await embed_documents([c.content for c in chunks])

        # BM25 length normalisation uses this document's own mean chunk length.
        lengths = [float(len(bm25.tokenize(c.content))) for c in chunks]
        avgdl = sum(lengths) / len(lengths) if lengths else 0.0
        sparse_vectors = [
            bm25.chunk_sparse_vector(c.content, avgdl) for c in chunks
        ]
        logger.info(
            "document %s: embedded %d dense + %d sparse in %.2fs (avgdl=%.1f)",
            document_id,
            len(dense_vectors),
            len(sparse_vectors),
            time.perf_counter() - stage,
            avgdl,
        )

        await set_status(document_id, DocumentStatus.INDEXING)
        stage = time.perf_counter()

        async with session_scope() as session:
            document = await session.scalar(
                select(Document).where(Document.id == document_id)
            )
            if document is None:
                raise RuntimeError(f"document {document_id} vanished before indexing")
            filename = document.filename

        points: list[PointStruct] = []
        rows: list[DocumentChunk] = []
        point_ids: list[str] = []

        for chunk, dense, sparse in zip(chunks, dense_vectors, sparse_vectors, strict=True):
            point_id = str(uuid.uuid4())
            point_ids.append(point_id)
            points.append(
                PointStruct(
                    id=point_id,
                    vector={DENSE_VECTOR: dense, SPARSE_VECTOR: sparse},
                    payload={
                        "document_id": str(document_id),
                        "filename": filename,
                        "chunk_index": chunk.chunk_index,
                        "page_number": chunk.page_number,
                        "section": chunk.section,
                        "heading_path": chunk.heading_path,
                        "content": chunk.content,
                        "content_type": chunk.content_type,
                    },
                )
            )
            rows.append(
                DocumentChunk(
                    document_id=document_id,
                    chunk_index=chunk.chunk_index,
                    page_number=chunk.page_number,
                    section=chunk.section,
                    qdrant_point_id=point_id,
                )
            )

        await get_qdrant_client().upsert(
            collection_name=collection, points=points, wait=True
        )

        async with session_scope() as session:
            session.add_all(rows)

        # Corpus just changed, so cached IDF is stale.
        corpus_stats.invalidate()

        logger.info(
            "document %s: indexed %d point(s) in %.2fs",
            document_id,
            len(points),
            time.perf_counter() - stage,
        )

        await set_status(document_id, DocumentStatus.READY)
        logger.info(
            "document %s: READY with %d chunk(s) in %.2fs total",
            document_id,
            len(chunks),
            time.perf_counter() - started,
        )
        return len(chunks)

    except asyncio.CancelledError:
        # e.g. a dev-server reload during ingestion. Without this the row would
        # sit in a non-terminal stage forever and the sidebar would spin.
        logger.warning(
            "document %s: ingestion CANCELLED after %.2fs", document_id, time.perf_counter() - started
        )
        await _mark_failed_best_effort(document_id, "ingestion cancelled before completion")
        raise
    except Exception as exc:
        logger.exception("document %s: ingestion FAILED: %s", document_id, exc)
        await _mark_failed_best_effort(document_id, str(exc))
        raise
