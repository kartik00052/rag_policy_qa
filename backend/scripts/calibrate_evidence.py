"""Calibrate the Stage 4 evidence threshold against the real corpus.

PROJECT.md Section 6 makes insufficient evidence a hard requirement, and
WORKFLOW.md Rule 5 requires the threshold to be explicit and documented rather
than a feeling. This script measures what the cross-encoder actually produces so
the number in ``Settings.evidence_min_score`` is derived, not guessed.

Every question below is labelled against the *actual* ingested document
(``acme_travel_policy.pdf``), not against a guess about what a travel policy
usually contains. The labels were checked by reading all 13 indexed chunks.

It separates two distributions:

* **answerable** - the document genuinely answers it.
* **absent** - the document says nothing about it. These span wrong domain,
  wrong company, and plausible-but-missing specifics.

A cross-encoder will always find *something* topically adjacent, so the real
question is not "is the gap clean" but "where does the trade-off sit". The
script therefore sweeps thresholds and reports the confusion matrix, and prints
the model's own natural boundary (logit 0 = p 0.5) for comparison.

Run: ``uv run python -m scripts.calibrate_evidence`` (Qdrant up, docs ingested).
"""

from __future__ import annotations

import asyncio
import uuid

from app.core.config import get_settings
from app.schemas.retrieval import RetrievedChunk
from app.services.qdrant import get_qdrant_client
from app.services.reranker import rerank

#: Answered by acme_travel_policy.pdf. The expected answer is noted so a
#: mislabel is obvious when the retrieved chunk is printed.
ANSWERABLE: list[tuple[str, str]] = [
    ("How many days of annual leave do I get per year?", "cap 25 days/leave year, 1.75 days/month"),
    ("How much is the nightly hotel cap in New York?", "Band A, 1500 USD"),
    ("How far in advance must I request travel approval?", "5 working days"),
    ("What is the daily meal allowance for international travel?", "100 USD"),
    ("What is the cap on taxi reimbursement per day?", "120 USD/day, receipts required"),
    ("How much parental leave does the primary caregiver get?", "18 weeks"),
    ("How much sick leave do I get and may I carry any over?", "10 days/year, 5 carry forward"),
]

#: Genuinely absent from the document.
ABSENT: list[tuple[str, str]] = [
    ("What is the home office internet reimbursement cap?", "not in document"),
    ("How much is the annual performance bonus?", "not in document"),
    ("What is the gym membership subsidy?", "not in document"),
    ("What is the probation period length?", "not in document"),
    ("How do I claim tuition reimbursement?", "not in document"),
    ("What is the notice period when resigning?", "not in document"),
    ("How much severance is paid on redundancy?", "not in document"),
    ("What is the parking reimbursement at the head office?", "not in document"),
    ("How many sick leave days do part-time employees accrue?", "document states full-time only"),
]

#: Thresholds to report. 0.0 is included because it is the cross-encoder's own
#: decision boundary (raw logit 0 == sigmoid 0.5) rather than a fitted value.
CANDIDATE_THRESHOLDS = [-2.0, -1.0, 0.0, 1.0, 2.0, 3.0, 5.0]


async def load_chunks() -> list[RetrievedChunk]:
    """Every indexed chunk, as a flat candidate list for scoring."""
    settings = get_settings()
    client = get_qdrant_client()
    chunks: list[RetrievedChunk] = []
    offset = None
    while True:
        points, offset = await client.scroll(
            collection_name=settings.qdrant_collection,
            limit=256,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        for point in points:
            payload = point.payload or {}
            chunks.append(
                RetrievedChunk(
                    point_id=str(point.id),
                    score=0.0,
                    document_id=uuid.UUID(payload["document_id"]),
                    filename=payload.get("filename", ""),
                    chunk_index=int(payload.get("chunk_index", 0)),
                    page_number=payload.get("page_number"),
                    section=payload.get("section", ""),
                    heading_path=list(payload.get("heading_path") or []),
                    content=payload.get("content", ""),
                    content_type=payload.get("content_type", "text"),
                )
            )
        if offset is None:
            break
    return chunks


async def score_all(
    chunks: list[RetrievedChunk], cases: list[tuple[str, str]]
) -> list[tuple[str, str, float, str]]:
    """Best cross-encoder score per question, with the chunk that earned it."""
    out: list[tuple[str, str, float, str]] = []
    for question, expected in cases:
        ranked = await rerank(question, chunks, top_k=1)
        if not ranked:
            out.append((question, expected, float("-inf"), ""))
            continue
        top = ranked[0]
        preview = " ".join(top.chunk.content.split())[:58]
        out.append((question, expected, top.score, preview))
    return out


def report(rows: list[tuple[str, str, float, str]]) -> None:
    for question, expected, score, preview in rows:
        print(f"  {score:7.3f}  {question}")
        print(f"            expected: {expected}")
        print(f"            top chunk: {preview}...")


def main_sync(
    answerable: list[tuple[str, str, float, str]],
    absent: list[tuple[str, str, float, str]],
) -> int:
    report(answerable)
    print()
    report(absent)

    answerable_scores = [r[2] for r in answerable]
    absent_scores = [r[2] for r in absent]

    print("\n" + "=" * 72)
    print(f"answerable  n={len(answerable_scores)}  min={min(answerable_scores):.3f}  max={max(answerable_scores):.3f}")
    print(f"absent      n={len(absent_scores)}  min={min(absent_scores):.3f}  max={max(absent_scores):.3f}")
    print("=" * 72)

    boundary = 0.0
    false_negatives = [r for r in answerable if r[2] < boundary]
    false_positives = [r for r in absent if r[2] >= boundary]
    print(f"\nat the chosen boundary ({boundary:.1f}) the two failure modes are:")
    if false_negatives:
        print("  false negatives - document answers it, cross-encoder scored it low:")
        for question, expected, score, preview in false_negatives:
            print(f"    {score:7.3f}  {question}")
    if false_positives:
        print("  false positives - document does not answer it, cross-encoder scored it high:")
        for question, expected, score, preview in false_positives:
            print(f"    {score:7.3f}  {question}")
    print(
        "\nBoth are worth knowing about before tuning this further:\n"
        "  * False negatives come from table content and paraphrase mismatch\n"
        "    ('nightly hotel cap' vs a markdown table with a 'Nightly cap' column).\n"
        "    A cross-encoder reads the flat table text, not the table semantics.\n"
        "  * False positives come from lexical near-matches where the document\n"
        "    answers a *different subject* than the question asks. A high score\n"
        "    means 'this passage is about that', not 'this passage answers you'.\n"
        "    Only the generation step can catch the second kind, which is why the\n"
        "    prompt forbids the model from generalising beyond what it is given."
    )

    print(f"\n{'threshold':>10}  {'TP':>3} {'FN':>3} {'FP':>3} {'TN':>3}  {'precision':>9} {'recall':>7}")
    best = None
    for threshold in CANDIDATE_THRESHOLDS:
        tp = sum(1 for _, _, s, _ in answerable if s >= threshold)
        fn = len(answerable) - tp
        fp = sum(1 for _, _, s, _ in absent if s >= threshold)
        tn = len(absent) - fp
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / len(answerable)
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        marker = ""
        if threshold == 0.0:
            marker = "  <- model decision boundary"
        print(
            f"{threshold:>10.1f}  {tp:>3} {fn:>3} {fp:>3} {tn:>3}  "
            f"{precision:>9.2f} {recall:>7.2f}   f1={f1:.2f}{marker}"
        )
        if best is None or f1 > best[1]:
            best = (threshold, f1, precision, recall)
    print(
        f"\nbest f1 at threshold={best[0]:.1f} (precision={best[2]:.2f} recall={best[3]:.2f})"
    )
    print(
        "Note: 0.0 is preferred unless another threshold is clearly better, because it\n"
        "is the model's own calibrated boundary and needs no fitting to this corpus."
    )
    return 0


async def main() -> int:
    chunks = await load_chunks()
    if not chunks:
        print("no indexed chunks - ingest a document first")
        return 1
    print(f"corpus: {len(chunks)} chunk(s)\n")
    answerable = await score_all(chunks, ANSWERABLE)
    absent = await score_all(chunks, ABSENT)
    return main_sync(answerable, absent)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
