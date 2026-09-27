"""Purge every trace of one document's chunks from both stores.

Exists as its own module because of an import constraint: ``app.services.documents``
imports ``ingest_document`` from ``app.ingestion.pipeline``, so the pipeline cannot
import back from ``app.services.documents``. Both need this cleanup, so it lives
here, where it can import the Qdrant client, the session factory and the models
without creating a cycle (WORKFLOW.md Section 4: one responsibility per module).
"""

from __future__ import annotations

import uuid

from sqlalchemy import delete

from app.core.logging import get_logger
from app.db.models import DocumentChunk
from app.db.session import session_scope
from app.services.corpus_stats import corpus_stats
from app.services.qdrant import delete_document_points

logger = get_logger(__name__)


async def purge_document_chunks(document_id: uuid.UUID) -> int:
    """Delete a document's Qdrant points and its ``document_chunks`` rows.

    Both stores are cleared together, on purpose. PROJECT.md Section 5 makes
    ``document_chunks`` the record of what exists, and the Stage 3 checkpoint
    asserts that Qdrant's point count equals the ``document_chunks`` count, so
    purging only one side would trade an orphan for a count mismatch.

    Qdrant is scoped by a payload filter on ``document_id`` rather than by a list
    of known point ids, because the caller that most needs this - startup
    reconciliation - is recovering from a process that died and no longer has
    that list in memory.

    Returns the number of Qdrant points removed.
    """
    points = await delete_document_points(document_id)
    async with session_scope() as session:
        await session.execute(
            delete(DocumentChunk).where(DocumentChunk.document_id == document_id)
        )
    # Cached BM25 IDF is derived from the collection payload, which just changed.
    corpus_stats.invalidate()
    logger.info(
        "purged %d qdrant point(s) and all document_chunks rows for %s",
        points,
        document_id,
    )
    return points
