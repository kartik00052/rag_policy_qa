"""Remove simulated crash-simulation rows and any vectors they left behind.

The earlier "simulated hard crash" test inserted bare ``Document`` rows directly
into Postgres (filename prefix ``crash_``) to imitate a process dying mid
pipeline. Those are test artifacts, not real uploads, so they must not be present
during a supposedly-clean verification run.

They are deleted from Postgres *and* any Qdrant points carrying their
``document_id`` are swept. The sweep matters: a ``failed`` document is not in
``IN_PROGRESS_STATUSES``, so startup reconciliation never looks at one, and any
vectors it left behind would silently persist and remain retrievable.

Usage: python -m scripts.cleanup_simulated_rows
"""

from __future__ import annotations

import asyncio

from sqlalchemy import delete, func, select

from app.core.config import IN_PROGRESS_STATUSES
from app.core.eventloop import configure_event_loop
from app.core.logging import configure_logging
from app.db.models import Document, DocumentChunk
from app.db.session import session_scope
from app.services.corpus_stats import corpus_stats
from app.services.qdrant import close_qdrant, delete_document_points, get_qdrant_client

SIMULATION_PREFIX = "crash_"


async def main() -> int:
    configure_logging("WARNING")

    doomed: dict[object, str] = {}
    stragglers: list[str] = []
    async with session_scope() as session:
        simulated = (
            (
                await session.execute(
                    select(Document).where(
                        Document.filename.like(f"{SIMULATION_PREFIX}%")
                    )
                )
            )
            .scalars()
            .all()
        )
        # Anything still sitting in a non-terminal stage is also a leftover, but
        # in normal operation the running server reconciles those at boot, so
        # only report them rather than deleting.
        staged = [s.value for s in IN_PROGRESS_STATUSES]
        stragglers = (
            (
                await session.execute(
                    select(Document).where(Document.status.in_(staged))
                )
            )
            .scalars()
            .all()
        )
        doomed = {d.id: d.filename for d in simulated}
        stragglers = [
            f"{str(d.id)[:8]} status={d.status} {d.filename}" for d in stragglers
        ]

        for document_id, filename in doomed.items():
            print(f"  removing simulated row {str(document_id)[:8]}  {filename}")
        if doomed:
            # document_chunks has ON DELETE CASCADE, so rows go with it.
            await session.execute(delete(Document).where(Document.id.in_(list(doomed))))

    for document_id in doomed:
        removed = await delete_document_points(document_id)
        if removed:
            print(f"  swept {removed} orphaned Qdrant point(s) for {str(document_id)[:8]}")
    corpus_stats.invalidate()

    client = get_qdrant_client()
    collection = (await client.get_collections()).collections[0].name
    qdrant_total = (await client.count(collection, exact=True)).count

    async with session_scope() as session:
        docs = await session.scalar(select(func.count(Document.id)))
        chunks = await session.scalar(select(func.count(DocumentChunk.id)))
        remaining = (
            (await session.execute(select(Document.id, Document.filename, Document.status))).all()
        )

    print("\n--- state after cleanup ---")
    for row in remaining:
        print(f"  {str(row[0])[:8]}  status={row[2]:<11} {row[1]}")
    print(f"  documents={docs} document_chunks={chunks} qdrant_points={qdrant_total}")
    if stragglers:
        print(f"\n  note: {len(stragglers)} document(s) still in a non-terminal stage:")
        for line in stragglers:
            print(f"    {line}")

    await close_qdrant()
    return 0


if __name__ == "__main__":
    configure_event_loop()
    raise SystemExit(asyncio.run(main()))
