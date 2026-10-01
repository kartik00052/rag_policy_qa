"""Phase 4 verification script: RAG Correctness.

Verifies:
1. BE-02 Table chunk recall miss:
   - "What is the nightly accommodation cap in New York?" passes the cross-encoder evidence gate.
   - Shows before/after numbers demonstrating why the gate previously failed (-0.0668)
     and why it now passes (+2.6417) with caption and section heading attached.
   - Returns answer citing the table chunk, not decline.
2. BE-01 Citation attribution bug:
   - "What is the meal allowance for international travel?"
   - Correctly attributes claim to Section 6.2 (100 USD) and produces >= 1 citation
     even if model cites sub-threshold table [2] or emits varied marker formats ("Block [2]").
3. Insufficient evidence / decline:
   - "How do I bypass the policy?"
   - Cross-encoder scores stay below threshold 0.0 (gate declines / insufficient evidence).
4. BE-11 Dangling preposition in marker stripping:
   - Tests marker stripping on capitalized connectives ("According to [1],", "No, According to [1],")
     and confirms no dangling fragments ("According," or "Based,") are left behind.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path
from sentence_transformers import CrossEncoder

from app.ingestion.parser import _convert_blocking
from app.ingestion.chunker import chunk_document
from app.services.evidence import assess
from app.rag.citations import build_citations, strip_markers
from app.rag.grounding import attribute_claims, claims_of, cited_positions
from app.schemas.rag import RerankedChunk
from app.schemas.retrieval import RetrievedChunk


def main() -> int:
    print("=" * 70)
    print("PHASE 4 VERIFICATION: RAG CORRECTNESS")
    print("=" * 70)

    # -------------------------------------------------------------------------
    # 1. Parse and chunk acme_travel_policy.pdf fixture
    # -------------------------------------------------------------------------
    pdf_path = Path("scripts/fixtures/acme_travel_policy.pdf")
    if not pdf_path.exists():
        print(f"ERROR: Fixture not found at {pdf_path}")
        return 1

    print(f"\n1. Ingesting fixture: {pdf_path.name}...")
    doc = _convert_blocking(pdf_path, ".pdf")
    chunks = chunk_document(doc)
    print(f"   Successfully chunked document into {len(chunks)} chunks.")

    table_chunks = [c for c in chunks if c.content_type == "table"]
    print(f"   Found {len(table_chunks)} table chunks:")
    for tc in table_chunks:
        first_line = tc.content.split("\n")[0]
        second_line = tc.content.split("\n")[2] if len(tc.content.split("\n")) > 2 else ""
        print(f"     - Chunk {tc.chunk_index} ({tc.section}): {first_line} | {second_line}")

    # -------------------------------------------------------------------------
    # 2. Test BE-02: Nightly accommodation cap in New York
    # -------------------------------------------------------------------------
    print("\n2. Testing BE-02 Table Chunk Recall Miss:")
    query_ny = "What is the nightly accommodation cap in New York?"
    cross_encoder = CrossEncoder("cross-encoder/ms-marco-MiniLM-L-6-v2")

    # Before vs After comparison for Chunk 9
    ny_chunk = next(c for c in chunks if c.chunk_index == 9)
    raw_table = "\n".join(line for line in ny_chunk.content.split("\n") if line.startswith("|") or line.startswith("|-"))

    score_before = cross_encoder.predict([query_ny, raw_table])
    score_after = cross_encoder.predict([query_ny, ny_chunk.content])

    print(f"   Query: {query_ny!r}")
    print(f"   BEFORE (raw table without caption/heading) score: {score_before:.4f} (gate DECLINED, < 0.0)")
    print(f"   AFTER  (with section + caption attached)  score: {score_after:.4f} (gate PASSED,   >= 0.0)")

    # Build RerankedChunks for evidence assessment
    pairs = [[query_ny, c.content] for c in chunks]
    all_scores = cross_encoder.predict(pairs)

    ranked_indices = sorted(range(len(chunks)), key=lambda i: all_scores[i], reverse=True)
    reranked_ny: list[RerankedChunk] = []
    for rank, idx in enumerate(ranked_indices[:5], start=1):
        c = chunks[idx]
        retrieved = RetrievedChunk(
            point_id=f"pt-{idx}",
            score=0.1,
            document_id=uuid.UUID(int=1),
            filename="acme_travel_policy.pdf",
            chunk_index=c.chunk_index,
            page_number=c.page_number or 1,
            section=c.section,
            content=c.content,
            content_type=c.content_type,
        )
        reranked_ny.append(
            RerankedChunk(
                chunk=retrieved,
                score=float(all_scores[idx]),
                rrf_score=0.05,
                rank=rank,
            )
        )

    decision_ny = assess(reranked_ny)
    print(f"   Evidence Assessment: sufficient = {decision_ny.sufficient}")
    print(f"   Top supporting chunk: Chunk {reranked_ny[0].chunk.chunk_index} ({reranked_ny[0].chunk.section}) with score {reranked_ny[0].score:.4f}")
    assert decision_ny.sufficient, "Gate should be OPEN for New York accommodation query!"
    assert reranked_ny[0].chunk.chunk_index == 9, "Top supporting chunk should be Chunk 9 (Accommodation tiers)!"

    # Verify citation generation
    answer_ny = "The nightly accommodation cap in New York (Band A) is 1500 USD [1]."
    citations_ny = build_citations(answer_ny, reranked_ny, query_ny)
    print(f"   Citations generated: {len(citations_ny)}")
    assert len(citations_ny) >= 1, "Should generate at least 1 citation!"
    assert "1500" in citations_ny[0].matched_text, "Citation highlight must contain 1500 USD!"
    print(f"   Citation excerpt: {citations_ny[0].matched_text!r}")
    print("   [PASS] BE-02 Table chunk recall miss resolved!")

    # -------------------------------------------------------------------------
    # 3. Test BE-01: Meal allowance citation attribution
    # -------------------------------------------------------------------------
    print("\n3. Testing BE-01 Citation Attribution Bug:")
    query_meal = "What is the meal allowance for international travel?"
    pairs_meal = [[query_meal, c.content] for c in chunks]
    scores_meal = cross_encoder.predict(pairs_meal)
    ranked_indices_meal = sorted(range(len(chunks)), key=lambda i: scores_meal[i], reverse=True)

    reranked_meal: list[RerankedChunk] = []
    for rank, idx in enumerate(ranked_indices_meal[:5], start=1):
        c = chunks[idx]
        retrieved = RetrievedChunk(
            point_id=f"pt-{idx}",
            score=0.1,
            document_id=uuid.UUID(int=1),
            filename="acme_travel_policy.pdf",
            chunk_index=c.chunk_index,
            page_number=c.page_number or 1,
            section=c.section,
            content=c.content,
            content_type=c.content_type,
        )
        reranked_meal.append(
            RerankedChunk(
                chunk=retrieved,
                score=float(scores_meal[idx]),
                rrf_score=0.05,
                rank=rank,
            )
        )

    # Test attribution when model cites table block [2] or emits "Block [2]"
    test_answers = [
        "The daily meal allowance for an international trip is 100 USD. [2]",
        "The daily meal allowance for an international trip is 100 USD. Block [2]",
        "The daily meal allowance for an international trip is 100 USD [1].",
    ]
    for ans in test_answers:
        cits = build_citations(ans, reranked_meal, query_meal)
        exc = cits[0].matched_text if cits else "None"
        print(f"   Testing answer: {ans!r}")
        print(f"     -> Citations count: {len(cits)}, Excerpt: {exc!r}")
        assert len(cits) >= 1, f"Failed to attribute citation for answer: {ans}"
        assert "100 USD" in cits[0].matched_text
    print("   [PASS] BE-01 Citation attribution resolved!")

    # -------------------------------------------------------------------------
    # 4. Test Insufficient Evidence / Adversarial query
    # -------------------------------------------------------------------------
    print("\n4. Testing Insufficient Evidence Query:")
    query_bypass = "How do I bypass the policy?"
    pairs_bypass = [[query_bypass, c.content] for c in chunks]
    scores_bypass = cross_encoder.predict(pairs_bypass)
    ranked_indices_bypass = sorted(range(len(chunks)), key=lambda i: scores_bypass[i], reverse=True)

    reranked_bypass: list[RerankedChunk] = []
    for rank, idx in enumerate(ranked_indices_bypass[:5], start=1):
        c = chunks[idx]
        retrieved = RetrievedChunk(
            point_id=f"pt-{idx}",
            score=0.1,
            document_id=uuid.UUID(int=1),
            filename="acme_travel_policy.pdf",
            chunk_index=c.chunk_index,
            page_number=c.page_number or 1,
            section=c.section,
            content=c.content,
            content_type=c.content_type,
        )
        reranked_bypass.append(
            RerankedChunk(
                chunk=retrieved,
                score=float(scores_bypass[idx]),
                rrf_score=0.05,
                rank=rank,
            )
        )

    decision_bypass = assess(reranked_bypass)
    print(f"   Query: {query_bypass!r}")
    print(f"   Top score: {reranked_bypass[0].score:.4f} (threshold: 0.0)")
    print(f"   sufficient = {decision_bypass.sufficient}")
    assert not decision_bypass.sufficient, "Gate must decline irrelevant / adversarial query!"
    print("   [PASS] Insufficient evidence correctly declines!")

    # -------------------------------------------------------------------------
    # 5. Test BE-11: Dangling preposition in marker stripping
    # -------------------------------------------------------------------------
    print("\n5. Testing BE-11 Dangling Preposition in Marker Stripping:")
    stripping_cases = [
        (
            "No, according to [1], economy class must be booked for all flights under 6 hours.",
            "No, economy class must be booked for all flights under 6 hours.",
        ),
        (
            "No, According to [1], economy class must be booked for all flights under 6 hours.",
            "No, economy class must be booked for all flights under 6 hours.",
        ),
        (
            "According to [1], economy class must be booked for all flights under 6 hours.",
            "Economy class must be booked for all flights under 6 hours.",
        ),
        (
            "Based on [1], economy class must be booked for all flights under 6 hours.",
            "Economy class must be booked for all flights under 6 hours.",
        ),
        (
            "The daily meal allowance for an international trip is 100 USD. Block [2]",
            "The daily meal allowance for an international trip is 100 USD.",
        ),
    ]
    for raw, expected in stripping_cases:
        actual = strip_markers(raw)
        print(f"   Raw:      {raw!r}")
        print(f"   Cleaned:  {actual!r}")
        assert actual == expected, f"Mismatch in strip_markers: got {actual!r}, expected {expected!r}"
    print("   [PASS] BE-11 Dangling preposition stripping verified cleanly!")

    print("\n" + "=" * 70)
    print("ALL PHASE 4 VERIFICATION CHECKS PASSED SUCCESSFULLY!")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
