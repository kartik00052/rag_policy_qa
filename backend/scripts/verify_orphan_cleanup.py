"""Real partial-failure test: does a document that dies mid-ingestion leave
orphaned chunk points in Qdrant?

Why this exists
---------------
The earlier "simulated hard crash" test only INSERTed bare ``Document`` rows with
status=embedding/parsing/uploading. It never ran ingestion, so it never wrote a
single point to Qdrant, and therefore could not have detected an orphan even if
one existed. It proved the status column could be set; it proved nothing about
the vector store.

An orphaned point matters: retrieval filters on nothing, so a point belonging to
a document that is not ``ready`` is still returned, still reranked, and still
citable as evidence. PROJECT.md's core promise is that every answer cites a
verifiable source, so a half-ingested policy must not be quotable.

Three real failure windows are exercised, all *after* at least one point has been
written to Qdrant:

  A. Postgres write of the ``document_chunks`` rows fails, immediately after the
     Qdrant upsert. Worst case: vectors in Qdrant, nothing in Postgres at all.
  B. The transition to ``ready`` fails, after both stores were written. Vectors
     and rows exist but the document is not usable.
  C. The process is killed outright (``BaseException``, so no ``except`` clause
     can run - the faithful analogue of SIGKILL) after the upsert. Nothing in
     this process can clean up; only the next boot's reconciliation pass can.

The test asserts the *desired* end state: zero orphaned points in every case.
Run against the unfixed pipeline it fails, which is the point.

Usage: python -m scripts.verify_orphan_cleanup
"""

from __future__ import annotations

import asyncio
import shutil
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from qdrant_client.http.models import FieldCondition, Filter, MatchValue
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import DocumentStatus
from app.core.eventloop import configure_event_loop
from app.core.logging import configure_logging, get_logger
from app.db.models import Document, DocumentChunk
from app.db.session import get_engine, session_scope
from app.ingestion import pipeline
from app.services.corpus_stats import corpus_stats
from app.services.documents import reconcile_interrupted_documents
from app.services.qdrant import close_qdrant, delete_document_points, get_qdrant_client

logger = get_logger(__name__)

FIXTURE = Path(__file__).parent / "fixtures" / "acme_travel_policy.pdf"
STORAGE = Path(__file__).resolve().parents[1] / "storage"


class SimulatedHardCrash(BaseException):
    """Deliberately NOT an ``Exception``.

    ``ingest_document`` catches ``Exception`` to mark a document failed, so
    raising a ``BaseException`` reproduces a process that died where no handler
    runs - the only case startup reconciliation can actually save.
    """


@dataclass
class Outcome:
    label: str
    #: Snapshot taken immediately after the injected failure, *before* any
    #: recovery runs. This is the evidence of the bug.
    status: str | None
    qdrant_points: int
    pg_chunks: int
    #: True when the in-process failure handler cannot have run, so only boot
    #: reconciliation can recover this document.
    needs_boot_reconciliation: bool = False
    #: Snapshot taken after recovery. These are what the assertions use.
    final_status: str | None = None
    final_qdrant: int = 0
    final_pg: int = 0


def _scope(document_id: uuid.UUID) -> Filter:
    return Filter(
        must=[FieldCondition(key="document_id", match=MatchValue(value=str(document_id)))]
    )


async def _qdrant_count(document_id: uuid.UUID) -> int:
    client = get_qdrant_client()
    from app.core.config import get_settings

    collection = get_settings().qdrant_collection
    return (await client.count(collection, count_filter=_scope(document_id), exact=True)).count


async def _register_document(document_id: uuid.UUID) -> Path:
    """Create a real document row plus a real file on disk, then return its path."""
    STORAGE.mkdir(parents=True, exist_ok=True)
    destination = STORAGE / f"{document_id}.pdf"
    shutil.copyfile(FIXTURE, destination)
    async with session_scope() as session:
        session.add(
            Document(
                id=document_id,
                filename=f"partial_failure_{document_id}.pdf",
                file_type="pdf",
                storage_path=str(destination),
                status=DocumentStatus.UPLOADING.value,
            )
        )
    return destination


async def _inspect(document_id: uuid.UUID) -> tuple[str | None, int, int]:
    async with session_scope() as session:
        status = await session.scalar(
            select(Document.status).where(Document.id == document_id)
        )
        pg_chunks = await session.scalar(
            select(func.count(DocumentChunk.id)).where(
                DocumentChunk.document_id == document_id
            )
        )
    return status, await _qdrant_count(document_id), pg_chunks


async def _teardown(document_id: uuid.UUID, path: Path) -> None:
    """Always remove the test document and its vectors, pass or fail."""
    await delete_document_points(document_id)
    async with session_scope() as session:
        await session.execute(delete(Document).where(Document.id == document_id))
    path.unlink(missing_ok=True)
    corpus_stats.invalidate()


# --------------------------------------------------------------------------
# Failure injection
# --------------------------------------------------------------------------


class _FailingChunkInsertSession(AsyncSession):
    """Session whose ``add_all`` always fails.

    ``add_all`` is called exactly once in ``ingest_document`` - to insert the
    ``document_chunks`` rows - so this fails precisely in the window between the
    Qdrant upsert and the status transition, without touching the other session
    uses (``set_status`` mutates a loaded row and never calls ``add_all``).

    The override is deliberately **synchronous**. In SQLAlchemy 2.0
    ``AsyncSession.add_all`` is inherited from ``Session`` and is not a coroutine
    function - only flush/commit are awaited. Declaring it ``async def`` returns an
    un-awaited coroutine that silently does nothing, so the "failure" never
    happens and the case passes for the wrong reason.
    """

    def add_all(self, instances, *args, **kwargs):  # type: ignore[override]
        raise RuntimeError(
            "simulated Postgres failure while inserting document_chunks rows"
        )


@asynccontextmanager
async def _failing_insert_scope():
    factory = async_sessionmaker(
        bind=get_engine(), expire_on_commit=False, class_=_FailingChunkInsertSession
    )
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def _run_case_a(document_id: uuid.UUID, path: Path) -> Outcome:
    """Postgres chunk-row insert fails right after the Qdrant upsert."""
    original = pipeline.session_scope
    pipeline.session_scope = _failing_insert_scope
    try:
        await pipeline.ingest_document(document_id, path)
    except Exception as exc:  # noqa: BLE001 - the point is that it fails
        logger.info("case A raised as expected: %s", exc)
    finally:
        pipeline.session_scope = original
    status, points, rows = await _inspect(document_id)
    return Outcome("A: pg chunk insert fails after upsert", status, points, rows, -1)


async def _run_case_b(document_id: uuid.UUID, path: Path) -> Outcome:
    """Transition to ``ready`` fails, after both stores were written."""
    original = pipeline.set_status

    async def failing_set_status(doc_id: uuid.UUID, status: DocumentStatus) -> None:
        if status is DocumentStatus.READY:
            raise RuntimeError("simulated failure while marking the document ready")
        await original(doc_id, status)

    pipeline.set_status = failing_set_status
    try:
        await pipeline.ingest_document(document_id, path)
    except Exception as exc:  # noqa: BLE001 - the point is that it fails
        logger.info("case B raised as expected: %s", exc)
    finally:
        pipeline.set_status = original
    status, points, rows = await _inspect(document_id)
    return Outcome("B: ready transition fails after both writes", status, points, rows, -1)


async def _run_case_c(document_id: uuid.UUID, path: Path) -> Outcome:
    """Process dies outright after the upsert; only a fresh boot can recover."""
    original = pipeline.set_status

    async def crashing_set_status(doc_id: uuid.UUID, status: DocumentStatus) -> None:
        if status is DocumentStatus.READY:
            raise SimulatedHardCrash("simulated SIGKILL after the Qdrant upsert")
        await original(doc_id, status)

    pipeline.set_status = crashing_set_status
    try:
        await pipeline.ingest_document(document_id, path)
    except SimulatedHardCrash as exc:
        logger.info("case C crashed as expected: %s", exc)
    finally:
        pipeline.set_status = original

    status, points, rows = await _inspect(document_id)
    # This is what the next process start does (app/main.py lifespan).
    return Outcome(
        "C: SIGKILL after upsert, then boot reconciliation",
        status,
        points,
        rows,
        needs_boot_reconciliation=True,
    )


async def main() -> int:
    configure_logging("INFO")
    if not FIXTURE.exists():
        print(f"fixture missing: {FIXTURE}")
        return 1

    results: list[Outcome] = []
    for runner in (_run_case_a, _run_case_b, _run_case_c):
        document_id = uuid.uuid4()
        path = await _register_document(document_id)
        try:
            outcome = await runner(document_id, path)
            # Recovery: the in-process failure handler has already run for A and
            # B. Case C simulated a process death, so no handler could have run
            # and only the next boot's reconciliation pass can recover it.
            if outcome.needs_boot_reconciliation:
                handled = await reconcile_interrupted_documents()
                logger.info("case C: reconciliation handled %d document(s)", handled)
            # Re-sample BOTH stores after recovery, so the comparison below is
            # like-for-like. Comparing a post-recovery Qdrant count against a
            # pre-recovery Postgres snapshot would report a phantom mismatch.
            (
                outcome.final_status,
                outcome.final_qdrant,
                outcome.final_pg,
            ) = await _inspect(document_id)
            results.append(outcome)
        finally:
            await _teardown(document_id, path)

    print("\n=== partial-failure orphan results ===")
    print(
        f"  {'case':<44} {'status':<10} | {'qdrant':>7} {'pg':>4} | "
        f"{'qdrant':>7} {'pg':>4}  verdict"
    )
    print(f"  {'':<44} {'after failure':<10} | {'':>7} {'':>4} | "
          f"{'after recovery':>7} {'':>4}")
    failures = 0
    for outcome in results:
        problems = []
        if outcome.final_status != DocumentStatus.READY.value:
            # Not ready: nothing may remain in either store, or the vectors are
            # retrievable and citable as evidence.
            if outcome.final_qdrant:
                problems.append(f"{outcome.final_qdrant} ORPHANED VECTOR(S)")
        if outcome.final_qdrant != outcome.final_pg:
            problems.append("QDRANT/POSTGRES DISAGREE")
        if problems:
            failures += 1
        verdict = f"  <-- {', '.join(problems)}" if problems else "  clean"
        print(
            f"  {outcome.label:<44} {str(outcome.final_status):<10} | "
            f"{outcome.qdrant_points:>7} {outcome.pg_chunks:>4} | "
            f"{outcome.final_qdrant:>7} {outcome.final_pg:>4}{verdict}"
        )

    # Left-over state must be as we found it.
    from app.core.config import get_settings

    client = get_qdrant_client()
    total = (
        await client.count(get_settings().qdrant_collection, exact=True)
    ).count
    print(f"\n  collection now holds {total} point(s) after teardown")

    if failures:
        print(
            f"\nORPHAN TEST FAILED: {failures}/{len(results)} case(s) left chunk "
            "points in Qdrant for a document that is not ready - those points are "
            "retrievable and citable as evidence."
        )
        return 1
    print(
        f"\nORPHAN TEST PASSED: all {len(results)} partial-failure case(s) left "
        "zero orphaned points in Qdrant."
    )
    return 0


if __name__ == "__main__":
    configure_event_loop()
    try:
        raise SystemExit(asyncio.run(main()))
    finally:
        asyncio.run(close_qdrant())
