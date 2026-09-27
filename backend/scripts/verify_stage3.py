"""Stage 3 checkpoint: prove hybrid retrieval is actually correct.

Three independent checks:

1. RRF arithmetic against hand-computed values (no libraries involved, so a bug
   in the fusion cannot hide behind the same bug in the expectation).
2. Cross-store inspection - scroll Qdrant directly and assert the stored
   metadata is right, not merely present, and that Qdrant and Postgres agree
   about which chunks exist and which documents are usable.
3. Ask a real question and confirm the chunk that actually answers it is in the
   fused top results.

Nothing here hardcodes a chunk count. Postgres is the source of truth for how
many chunks should exist (PROJECT.md Section 5), so the script keeps working
across a corpus of any size or composition - see the comments on
``MIN_TABLE_CHUNKS`` and ``verify_metadata_keys`` for the two places where a
fixture-derived constant would have been the wrong thing to assert.

Usage::

    python -m scripts.verify_stage3 [document_id]
"""

from __future__ import annotations

import asyncio
import sys
import uuid

from sqlalchemy import func, select

from app.core.config import DocumentStatus
from app.core.eventloop import configure_event_loop
from app.core.logging import configure_logging
from app.db.models import Document, DocumentChunk
from app.db.session import dispose_engine, session_scope
from app.services.embeddings import dense_dimension
from app.services.qdrant import close_qdrant, ensure_collection, get_qdrant_client
from app.services.retrieval import RRF_K, _Hit, hybrid_search, reciprocal_rank_fusion

#: Per-chunk metadata required by PROJECT.md Section 4, plus the two fields this
#: script itself depends on (``content_type`` is branched on below to find table
#: chunks; ``content`` is what the markdown-preservation check inspects). Kept as
#: one constant so the assertion and the code that relies on it cannot drift.
REQUIRED_PAYLOAD_KEYS: tuple[str, ...] = (
    "document_id",
    "chunk_index",
    "page_number",
    "section",
    "heading_path",
    "content",
    "content_type",
)

#: Floor for "tables survive the round-trip at all". Deliberately a minimum, not
#: an exact count: an exact number pins the assertion to today's sample fixture
#: and would fail the moment anyone adds or removes a table from it, which is a
#: change in test data rather than a regression in the pipeline. WORKFLOW.md
#: Section 6 asks this checkpoint to catch real regressions, not to freeze the
#: fixture. The load-bearing assertions are the two below it - that *every* table
#: chunk is real markdown, and that *no* text chunk is a table that the chunker
#: mislabelled - both of which hold for any document.
MIN_TABLE_CHUNKS = 1

_SCROLL_PAGE = 256

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    if not ok:
        failures.append(f"{label}: {detail}")
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f" - {detail}" if detail else ""))


def hit(pid: str, score: float) -> _Hit:
    return _Hit(point_id=pid, payload={}, score=score)


def verify_rrf_math() -> None:
    print("\n=== 1. RRF arithmetic (hand-computed) ===")
    # dense: A,B,C   sparse: B,A
    dense = [hit("A", 0.9), hit("B", 0.8), hit("C", 0.7)]
    sparse = [hit("B", 12.4), hit("A", 3.1)]
    fused = {s.hit.point_id: s for s in reciprocal_rank_fusion([dense, sparse])}

    expected = {
        "A": 1 / (RRF_K + 1) + 1 / (RRF_K + 2),  # dense#1, sparse#2
        "B": 1 / (RRF_K + 2) + 1 / (RRF_K + 1),  # dense#2, sparse#1
        "C": 1 / (RRF_K + 3),  # dense#3 only
    }
    for pid, want in expected.items():
        got = fused[pid].rrf_score
        check(
            f"RRF score for {pid} == 1/(k+rank) sum",
            abs(got - want) < 1e-12,
            f"got {got:.12f} want {want:.12f}",
        )

    check(
        "agreeing items (A,B) outrank single-arm item (C)",
        fused["A"].rrf_score > fused["C"].rrf_score,
        f"A={fused['A'].rrf_score:.6f} C={fused['C'].rrf_score:.6f}",
    )
    check(
        "per-arm ranks recorded",
        (fused["A"].dense_rank, fused["A"].sparse_rank) == (1, 2)
        and (fused["B"].dense_rank, fused["B"].sparse_rank) == (2, 1),
        f"A={(fused['A'].dense_rank, fused['A'].sparse_rank)} "
        f"B={(fused['B'].dense_rank, fused['B'].sparse_rank)}",
    )
    check(
        "raw scores kept and not fused together",
        abs(fused["B"].dense_score - 0.8) < 1e-9
        and abs(fused["B"].sparse_score - 12.4) < 1e-9,
        f"dense={fused['B'].dense_score} sparse={fused['B'].sparse_score}",
    )
    check(
        "ordering is stable and score-descending",
        [s.hit.point_id for s in reciprocal_rank_fusion([dense, sparse])][:2] == ["A", "B"],
    )


def verify_metadata_keys(records: list[dict]) -> None:
    """Assert every payload carries the Section 4 metadata keys.

    Reports *all* offending points rather than bailing on the first one, and does
    not abort the caller: a missing key must not stop the remaining checks from
    running, or a single bad payload would hide every other problem in the corpus.
    """
    offenders: list[str] = []
    for position, payload in enumerate(records):
        missing = [key for key in REQUIRED_PAYLOAD_KEYS if key not in payload]
        if missing:
            doc = str(payload.get("document_id", "<no document_id>"))[:8]
            idx = payload.get("chunk_index", "<no chunk_index>")
            offenders.append(f"point[{position}] doc={doc} idx={idx} missing={missing}")

    if offenders:
        detail = f"{len(offenders)} incomplete: " + "; ".join(offenders[:5])
    else:
        detail = f"{len(records)} checked, required={list(REQUIRED_PAYLOAD_KEYS)}"
    check(
        f"all {len(records)} payload(s) carry the Section 4 metadata keys",
        not offenders,
        detail,
    )

    # A payload with the key present but empty is not usable evidence either, so
    # check the keys that must carry a real value rather than merely exist.
    blank = [
        f"doc={str(p.get('document_id'))[:8]} idx={p.get('chunk_index')} "
        f"empty={key}"
        for p in records
        for key in ("document_id", "content", "content_type")
        if key in p and not p[key]
    ]
    check(
        "no payload has an empty document_id / content / content_type",
        not blank,
        "; ".join(blank[:5]),
    )


async def scroll_all_payloads(collection: str) -> list[dict]:
    """Page through the whole collection. Single ``limit``, matching the
    installed client's signature (collection_name, scroll_filter, limit, ...)."""
    client = get_qdrant_client()
    records: list[dict] = []
    offset = None
    while True:
        batch, offset = await client.scroll(
            collection_name=collection,
            limit=_SCROLL_PAGE,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        records.extend(record.payload for record in batch)
        if offset is None:
            break
    return records


async def verify_qdrant_payloads(document_id: str | None) -> None:
    print("\n=== 2. Qdrant payloads (direct inspection) ===")
    collection = await ensure_collection(dense_dimension())
    client = get_qdrant_client()
    total = (await client.count(collection, exact=True)).count

    # Cross-store consistency: Postgres is the source of truth for how many
    # chunks should exist, so a silent write failure on either side is caught.
    # No hardcoded chunk count anywhere - this is what lets the script survive a
    # corpus of any size.
    async with session_scope() as session:
        pg_chunks = (
            await session.execute(select(func.count(DocumentChunk.id)))
        ).scalar_one()
        ready_ids = set(
            (
                await session.execute(
                    select(Document.id).where(Document.status == DocumentStatus.READY)
                )
            )
            .scalars()
            .all()
        )
        # Keys are stringified on both sides: DocumentChunk.document_id is a
        # uuid.UUID column while the Qdrant payload stores str(document_id), so
        # comparing them directly would silently mismatch every document.
        pg_per_document = {
            str(doc_id): count
            for doc_id, count in (
                await session.execute(
                    select(DocumentChunk.document_id, func.count(DocumentChunk.id))
                    .group_by(DocumentChunk.document_id)
                )
            ).all()
        }

    check(
        "Qdrant point count matches Postgres document_chunks",
        total == pg_chunks,
        f"qdrant={total} postgres={pg_chunks}",
    )
    check("at least one document is ready", bool(ready_ids), f"ready={ready_ids}")

    records = await scroll_all_payloads(collection)
    check("scrolled every point back", len(records) == total, f"{len(records)}/{total}")

    verify_metadata_keys(records)

    docs = {p.get("document_id") for p in records}
    orphans = {d for d in docs if d not in {str(i) for i in ready_ids}}
    check(
        "every Qdrant point belongs to a ready Postgres document",
        not orphans,
        f"orphans={sorted(str(o)[:8] for o in orphans)}" if orphans else f"docs={len(docs)}",
    )
    if document_id:
        check(
            "requested document_id is indexed",
            document_id in docs,
            f"indexed={sorted(str(d)[:8] for d in docs)}",
        )

    # Per-document grouping: contiguity is a property of one document's chunk
    # sequence, so two documents each numbered 0..n-1 must not be concatenated
    # into a single 0..2n-1 range and called contiguous.
    per_doc: dict[str, list[int]] = {}
    for payload in records:
        per_doc.setdefault(str(payload.get("document_id")), []).append(
            payload.get("chunk_index")
        )
    for doc, indexes in sorted(per_doc.items()):
        check(
            f"chunk_index contiguous 0..n-1 for {doc[:8]}",
            sorted(indexes) == list(range(len(indexes))),
            f"{len(indexes)} chunk(s), indexes={sorted(indexes)[:8]}",
        )
        # Per-document count agreement catches a mismatch that an equal-and-opposite
        # error in another document would hide in the global total above.
        check(
            f"Qdrant point count matches Postgres for {doc[:8]}",
            len(indexes) == pg_per_document.get(doc, 0),
            f"qdrant={len(indexes)} postgres={pg_per_document.get(doc, 0)}",
        )

    # The invariant is "a recorded page number is 1-based", which holds for any
    # corpus. Flow formats (DOCX/XLSX/CSV) legitimately carry no page provenance
    # at all (see app/db/models.py), so an empty set is vacuously satisfied rather
    # than a failure.
    pages = sorted({p["page_number"] for p in records if p.get("page_number") is not None})
    check(
        "recorded page numbers are 1-based",
        all(page >= 1 for page in pages),
        f"pages={pages}" if pages else "no paginated chunks (flow-format corpus) - vacuous",
    )

    tables = [p for p in records if p.get("content_type") == "table"]
    check(
        "table chunks preserved",
        len(tables) >= MIN_TABLE_CHUNKS,
        f"count={len(tables)} (min {MIN_TABLE_CHUNKS})",
    )
    malformed = [
        p["content"][:40] for p in tables if "|" not in p["content"] or "---" not in p["content"]
    ]
    if malformed:
        detail = f"{len(malformed)} malformed: {malformed[:2]}"
    elif tables:
        detail = f"first table starts {tables[0]['content'][:40]!r}"
    else:
        detail = "no tables"
    check("every table chunk is markdown, not prose", not malformed, detail)
    # Inverse of the check above: a markdown table mislabelled as prose would be
    # unfindable by content_type and would silently stop being citable as a table.
    mislabelled = [
        str(p.get("document_id"))[:8]
        for p in records
        if p.get("content_type") != "table"
        and "|" in p.get("content", "")
        and "---" in p.get("content", "")
    ]
    check(
        "no non-table chunk contains an unlabelled markdown table",
        not mislabelled,
        f"mislabelled={mislabelled[:5]}",
    )

    check(
        "heading_path is a real breadcrumb, not empty",
        any(p.get("heading_path") for p in records),
        f"sample={next((p['heading_path'] for p in records if p.get('heading_path')), None)!r}",
    )

    print("\n  --- metadata as stored in Qdrant ---")
    # Sorted by (document_id, chunk_index) so a multi-document run prints grouped
    # and in chunk order, with document_id first and visible on every line.
    for payload in sorted(
        records, key=lambda p: (str(p.get("document_id")), p.get("chunk_index", -1))
    ):
        page = payload.get("page_number")
        print(
            f"   {str(payload.get('document_id'))[:8]} idx={payload.get('chunk_index'):<3} "
            f"page={page if page else '-':<3} type={payload.get('content_type'):<6} "
            f"section={payload.get('section')!r}"
        )
        print(f"        heading_path={payload.get('heading_path')!r}")


async def verify_real_question() -> None:
    print("\n=== 3. Real question -> fused ranking ===")
    query = "How long must an employee have worked before paid parental leave?"
    answer_needle = "six months"

    response = await hybrid_search(query, limit=5, candidate_limit=30)
    print(
        f"  candidates: dense={response.dense_candidates} sparse={response.sparse_candidates} "
        f"rrf_k={response.rrf_k} timings={response.timings.model_dump()}"
    )
    check("both arms returned candidates", response.dense_candidates and response.sparse_candidates)
    check("fusion produced results", bool(response.results), f"got {len(response.results)}")

    print("\n  --- fused top results ---")
    for rank, r in enumerate(response.results, start=1):
        arm = (
            f"dense#{r.dense_rank}({r.dense_score:.4f})" if r.dense_rank else "dense-"
        ) + " / " + (f"sparse#{r.sparse_rank}({r.sparse_score:.3f})" if r.sparse_rank else "sparse-")
        print(f"   {rank}. rrf={r.score:.6f}  {arm}")
        print(
            f"      page={r.page_number} section={r.section!r} "
            f"heading_path={r.heading_path!r}"
        )
        print(f"      {r.content[:150].replace(chr(10), ' ')}...")

    hit_rank = next(
        (i for i, r in enumerate(response.results, 1) if answer_needle in r.content.lower()),
        None,
    )
    check(
        f"chunk containing '{answer_needle}' is in the fused top results",
        hit_rank is not None,
        f"found at rank {hit_rank}" if hit_rank else "not found in any fused result",
    )
    if hit_rank:
        print(f"\n  -> answering chunk at fused rank {hit_rank}: {response.results[hit_rank - 1].content[:300]}")

    # A table question proves the table chunks are retrievable, not just present.
    table_query = "What is the accommodation cap for Band A?"
    table_response = await hybrid_search(table_query, limit=3, candidate_limit=30)
    table_hit = next(
        (i for i, r in enumerate(table_response.results, 1) if r.content_type == "table"),
        None,
    )
    check(
        "table chunk retrieved for a table-valued question",
        table_hit is not None,
        f"rank {table_hit}" if table_hit else f"top content_types={[r.content_type for r in table_response.results]}",
    )
    if table_hit:
        print(f"\n  -> table answer at rank {table_hit}: {table_response.results[table_hit - 1].content[:300]}")


async def main() -> int:
    configure_logging("WARNING")
    try:
        verify_rrf_math()
        target = sys.argv[1] if len(sys.argv) > 1 else None
        if target:
            uuid.UUID(target)  # fail fast on a malformed argument
        await verify_qdrant_payloads(target)
        await verify_real_question()
    finally:
        await close_qdrant()
        await dispose_engine()

    print()
    if failures:
        print(f"STAGE 3 FAILED ({len(failures)}):")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print(
        "STAGE 3 PASS: RRF arithmetic verified, Qdrant/Postgres agree with no "
        "orphans, fused ranking answers the question."
    )
    return 0


if __name__ == "__main__":
    configure_event_loop()
    raise SystemExit(asyncio.run(main()))
