"""Verify spreadsheet ingestion for .xlsx and .csv (Phase 5 / project.md Section 2).

Verifies the full pipeline:
1. Parse: Docling conversion of .csv and .xlsx files into structured DoclingDocument
2. Chunk: chunk_document preserving tables with content_type="table" and markdown formatting
3. Pages: extract_document_pages generating page elements for evidence viewer
4. Embed: Dense embedding (384-dim) and BM25 sparse vector generation
5. Query: Cross-encoder ranking correctly scoring factual queries against tabular data
6. Qdrant (when available): point creation, metadata schema validation, search, and cleanup

Usage:
    python -m scripts.verify_spreadsheet_ingestion
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path

from app.core.eventloop import configure_event_loop
from app.ingestion.parser import _convert_blocking, extract_document_pages
from app.ingestion.chunker import chunk_document
from app.services import bm25
from app.services.embeddings import embed_documents
from app.services.qdrant import (
    DENSE_VECTOR,
    SPARSE_VECTOR,
    ensure_collection,
    get_qdrant_client,
)
from app.services.chunk_purge import purge_document_chunks
from qdrant_client.http.models import PointStruct, SparseVector
from sentence_transformers import CrossEncoder

FIXTURES_DIR = Path("scripts/fixtures")
CSV_PATH = FIXTURES_DIR / "sample_policy_allowances.csv"
XLSX_PATH = FIXTURES_DIR / "sample_policy_accommodations.xlsx"


async def main() -> int:
    print("=" * 70)
    print("PHASE 5: SPREADSHEET INGESTION VERIFICATION (.XLSX & .CSV)")
    print("=" * 70)

    # -------------------------------------------------------------------------
    # 1. Verify CSV Ingestion (Parse -> Chunk -> Page Extraction)
    # -------------------------------------------------------------------------
    print(f"\n1. Ingesting CSV: {CSV_PATH.name} ({CSV_PATH.stat().st_size} bytes)...")
    assert CSV_PATH.exists(), f"Missing fixture {CSV_PATH}"
    doc_csv = _convert_blocking(CSV_PATH, ".csv")
    chunks_csv = chunk_document(doc_csv)
    pages_csv = extract_document_pages(doc_csv)

    print(f"   [PASS] Parsed CSV into DoclingDocument.")
    print(f"   Chunks produced: {len(chunks_csv)}")
    assert len(chunks_csv) >= 1, "CSV must produce at least one chunk"
    c_csv = chunks_csv[0]
    assert c_csv.content_type == "table", f"Expected content_type='table', got {c_csv.content_type}"
    assert "Destination" in c_csv.content, "Table header missing"
    assert "New York, USA" in c_csv.content and "100" in c_csv.content
    print(f"   [PASS] Chunk 0 content_type='{c_csv.content_type}' with markdown table structure.")
    print(f"   [PASS] Extracted {len(pages_csv)} page(s) for evidence viewer caching.")

    # -------------------------------------------------------------------------
    # 2. Verify XLSX Ingestion (Parse -> Chunk -> Page Extraction)
    # -------------------------------------------------------------------------
    print(f"\n2. Ingesting XLSX: {XLSX_PATH.name} ({XLSX_PATH.stat().st_size} bytes)...")
    assert XLSX_PATH.exists(), f"Missing fixture {XLSX_PATH}"
    doc_xlsx = _convert_blocking(XLSX_PATH, ".xlsx")
    chunks_xlsx = chunk_document(doc_xlsx)
    pages_xlsx = extract_document_pages(doc_xlsx)

    print(f"   [PASS] Parsed XLSX into DoclingDocument.")
    print(f"   Chunks produced: {len(chunks_xlsx)}")
    assert len(chunks_xlsx) >= 1, "XLSX must produce at least one chunk"
    c_xlsx = chunks_xlsx[0]
    assert c_xlsx.content_type == "table", f"Expected content_type='table', got {c_xlsx.content_type}"
    assert "City band" in c_xlsx.content, "Table header missing"
    assert "Band A" in c_xlsx.content and "1500" in c_xlsx.content
    print(f"   [PASS] Chunk 0 content_type='{c_xlsx.content_type}' with markdown table structure.")
    print(f"   [PASS] Extracted {len(pages_xlsx)} page(s) for evidence viewer caching.")

    # -------------------------------------------------------------------------
    # 3. Verify Embeddings (Dense & Sparse)
    # -------------------------------------------------------------------------
    print("\n3. Testing Dense and Sparse Embedding generation...")
    all_chunks = chunks_csv + chunks_xlsx
    dense_vecs = await embed_documents([c.content for c in all_chunks])
    assert len(dense_vecs) == len(all_chunks)
    assert len(dense_vecs[0]) == 384, f"Expected 384 dims, got {len(dense_vecs[0])}"
    print(f"   [PASS] Generated {len(dense_vecs)} dense vectors (dimension: {len(dense_vecs[0])}).")

    lengths = [float(len(bm25.tokenize(c.content))) for c in all_chunks]
    avgdl = sum(lengths) / len(lengths)
    sparse_vecs = [bm25.chunk_sparse_vector(c.content, avgdl) for c in all_chunks]
    assert len(sparse_vecs) == len(all_chunks)
    assert len(sparse_vecs[0].indices) > 0, "Sparse indices must not be empty"
    print(f"   [PASS] Generated {len(sparse_vecs)} BM25 sparse vectors (non-zero terms: {len(sparse_vecs[0].indices)}).")

    # -------------------------------------------------------------------------
    # 4. Verify RAG Scoring & Retrieval Relevance
    # -------------------------------------------------------------------------
    print("\n4. Testing Cross-Encoder Scoring on Spreadsheet Data...")
    cross_encoder = CrossEncoder("cross-encoder/ms-marco-MiniLM-L-6-v2")

    # Query matching CSV
    q_csv = "What is the daily allowance for London?"
    score_csv = float(cross_encoder.predict([q_csv, c_csv.content]))
    print(f"   Query: {q_csv!r}")
    print(f"   Score vs CSV table: {score_csv:.4f} (expected > 0.0)")
    assert score_csv > 0.0, f"Query should match CSV table content, got score {score_csv}"

    # Query matching XLSX
    q_xlsx = "What is the nightly accommodation cap for Band A?"
    score_xlsx = float(cross_encoder.predict([q_xlsx, c_xlsx.content]))
    print(f"   Query: {q_xlsx!r}")
    print(f"   Score vs XLSX table: {score_xlsx:.4f} (expected > 0.0)")
    assert score_xlsx > 0.0, f"Query should match XLSX table content, got score {score_xlsx}"

    # Irrelevant query
    q_irrel = "How do I configure nginx and reverse proxies?"
    score_irrel = float(cross_encoder.predict([q_irrel, c_csv.content]))
    print(f"   Query: {q_irrel!r}")
    print(f"   Score vs CSV table: {score_irrel:.4f} (expected < 0.0)")
    assert score_irrel < 0.0, f"Irrelevant query should score < 0.0, got {score_irrel}"
    print("   [PASS] Cross-encoder relevance discrimination verified on tabular data.")

    # -------------------------------------------------------------------------
    # 5. Verify Qdrant indexing & cleanup if available
    # -------------------------------------------------------------------------
    print("\n5. Checking Qdrant Live Connectivity...")
    qdrant = get_qdrant_client()
    try:
        await qdrant.get_collections()
        qdrant_available = True
    except Exception as exc:
        qdrant_available = False
        print(f"   Qdrant offline ({type(exc).__name__}). Live container test skipped.")

    if qdrant_available:
        print("   Qdrant is ONLINE. Running live index & cleanup test...")
        test_doc_id = uuid.uuid4()
        await ensure_collection(384)

        points = [
            PointStruct(
                id=str(uuid.uuid4()),
                vector={
                    DENSE_VECTOR: dense_vecs[0],
                    SPARSE_VECTOR: SparseVector(
                        indices=sparse_vecs[0].indices,
                        values=sparse_vecs[0].values,
                    ),
                },
                payload={
                    "document_id": str(test_doc_id),
                    "filename": CSV_PATH.name,
                    "chunk_index": 0,
                    "page_number": 1,
                    "section": None,
                    "heading_path": [],
                    "content": c_csv.content,
                    "content_type": "table",
                },
            )
        ]
        await qdrant.upsert(collection_name="policy_chunks", points=points)
        print("   [PASS] Upserted spreadsheet table point to Qdrant.")

        # Read back and verify payload
        retrieved = await qdrant.retrieve(
            collection_name="policy_chunks",
            ids=[points[0].id],
            with_payload=True,
        )
        assert len(retrieved) == 1
        assert retrieved[0].payload["content_type"] == "table"
        assert retrieved[0].payload["filename"] == CSV_PATH.name
        print("   [PASS] Point payload verified in Qdrant with correct metadata.")

        # Cleanup
        await purge_document_chunks(test_doc_id)
        post_purge = await qdrant.retrieve(
            collection_name="policy_chunks",
            ids=[points[0].id],
        )
        assert len(post_purge) == 0
        print("   [PASS] Test point purged cleanly from Qdrant.")

    print("\n" + "=" * 70)
    print("PHASE 5 VERIFICATION PASSED: .XLSX AND .CSV INGESTION PROVED")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    configure_event_loop()
    sys.exit(asyncio.run(main()))
