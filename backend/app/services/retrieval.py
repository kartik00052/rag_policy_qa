"""Hybrid retrieval: dense + sparse/BM25 fused with Reciprocal Rank Fusion.

Stage 3 only. No reranking, no LLM (PROJECT.md marks those Phase 2).

The two arms are queried separately because Qdrant scores a single named vector
per ``query_points`` call; fusion happens here in Python so the per-arm ranks and
scores stay inspectable. That is deliberate - Stage 3 has to be *provably*
correct, not merely plausible.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field

from qdrant_client.http import models

from app.core.logging import get_logger
from app.schemas.retrieval import (
    ArmTimings,
    HybridSearchResponse,
    RetrievedChunk,
)
from app.services.bm25 import query_sparse_vector
from app.services.corpus_stats import corpus_stats
from app.services.embeddings import dense_dimension, embed_query
from app.services.qdrant import DENSE_VECTOR, SPARSE_VECTOR, ensure_collection, get_qdrant_client

logger = get_logger(__name__)

#: RRF damping constant. 60 is the value from the original RRF paper and the
#: default used by most vector databases.
RRF_K = 60


@dataclass(slots=True)
class _Hit:
    point_id: str
    payload: dict
    score: float


@dataclass(slots=True)
class _Scored:
    """A fused result plus the per-arm evidence behind it."""

    hit: _Hit
    rrf_score: float = 0.0
    dense_score: float | None = None
    sparse_score: float | None = None
    dense_rank: int | None = None
    sparse_rank: int | None = None
    contributors: list[str] = field(default_factory=list)


def _build_filter(document_ids: list[uuid.UUID] | None) -> models.Filter | None:
    if not document_ids:
        return None
    return models.Filter(
        must=[
            models.FieldCondition(
                key="document_id",
                match=models.MatchValue(value=str(document_id)),
            )
            for document_id in document_ids
        ]
    )


async def dense_search(
    query: str, limit: int = 30, document_ids: list[uuid.UUID] | None = None
) -> list[_Hit]:
    """Cosine similarity over the ``dense`` named vector."""
    collection = await ensure_collection(dense_dimension())
    vector = await embed_query(query)
    response = await get_qdrant_client().query_points(
        collection_name=collection,
        query=vector,
        using=DENSE_VECTOR,
        query_filter=_build_filter(document_ids),
        limit=limit,
        with_payload=True,
    )
    return [
        _Hit(point_id=str(p.id), payload=p.payload or {}, score=float(p.score))
        for p in response.points
    ]


async def sparse_search(
    query: str, limit: int = 30, document_ids: list[uuid.UUID] | None = None
) -> list[_Hit]:
    """BM25 over the ``sparse`` named vector, using corpus IDF for the query."""
    collection = await ensure_collection(dense_dimension())
    stats = await corpus_stats.get()
    vector = query_sparse_vector(query, stats)
    if not vector.indices:
        logger.warning("query '%s' produced no usable BM25 terms", query)
        return []
    response = await get_qdrant_client().query_points(
        collection_name=collection,
        query=models.SparseVector(indices=vector.indices, values=vector.values),
        using=SPARSE_VECTOR,
        query_filter=_build_filter(document_ids),
        limit=limit,
        with_payload=True,
    )
    return [
        _Hit(point_id=str(p.id), payload=p.payload or {}, score=float(p.score))
        for p in response.points
    ]


def reciprocal_rank_fusion(
    rankings: list[list[_Hit]], k: int = RRF_K
) -> list[_Scored]:
    """Fuse ranked lists: score = sum over lists of 1 / (k + rank), rank from 1.

    Scores from different arms are never averaged - cosine and BM25 are not on a
    comparable scale. Only ranks are used, so each arm contributes equally.
    """
    fused: dict[str, _Scored] = {}
    arm_names = ("dense", "sparse")

    for arm_name, ranking in zip(arm_names, rankings, strict=False):
        for rank, hit in enumerate(ranking, start=1):
            entry = fused.get(hit.point_id)
            if entry is None:
                entry = _Scored(hit=hit)
                fused[hit.point_id] = entry
            entry.rrf_score += 1.0 / (k + rank)
            entry.contributors.append(f"{arm_name}#{rank}")
            if arm_name == "dense":
                entry.dense_score = hit.score
                entry.dense_rank = rank
            else:
                entry.sparse_score = hit.score
                entry.sparse_rank = rank

    # Stable ordering: score desc, then point id, so equal scores never reorder
    # between identical requests.
    return sorted(fused.values(), key=lambda s: (-s.rrf_score, s.hit.point_id))


def to_retrieved_chunks(fused: list[_Scored], limit: int) -> list[RetrievedChunk]:
    """Project fused results onto the public chunk shape.

    Split out of :func:`hybrid_search` so the Stage 5 graph can reuse the exact
    mapping instead of rebuilding it (WORKFLOW.md Section 2: one place owns the
    projection). Ranking semantics are unchanged.
    """
    return [
        RetrievedChunk(
            point_id=entry.hit.point_id,
            score=entry.rrf_score,
            document_id=uuid.UUID(entry.hit.payload["document_id"]),
            filename=entry.hit.payload.get("filename", ""),
            chunk_index=int(entry.hit.payload.get("chunk_index", 0)),
            page_number=entry.hit.payload.get("page_number"),
            section=entry.hit.payload.get("section", ""),
            heading_path=list(entry.hit.payload.get("heading_path") or []),
            content=entry.hit.payload.get("content", ""),
            content_type=entry.hit.payload.get("content_type", "text"),
            dense_score=entry.dense_score,
            sparse_score=entry.sparse_score,
            dense_rank=entry.dense_rank,
            sparse_rank=entry.sparse_rank,
        )
        for entry in fused[:limit]
    ]


def hits_to_retrieved_chunks(hits: list[_Hit]) -> list[RetrievedChunk]:
    """Project one arm's raw hits onto the public chunk shape.

    ``score`` is the arm's own score (cosine or BM25), not an RRF score. The
    two are not comparable across arms, which is exactly why fusion uses ranks;
    this projection exists for the debug surface, where the arm score is the
    point.
    """
    return [
        RetrievedChunk(
            point_id=hit.point_id,
            score=hit.score,
            document_id=uuid.UUID(hit.payload["document_id"]),
            filename=hit.payload.get("filename", ""),
            chunk_index=int(hit.payload.get("chunk_index", 0)),
            page_number=hit.payload.get("page_number"),
            section=hit.payload.get("section", ""),
            heading_path=list(hit.payload.get("heading_path") or []),
            content=hit.payload.get("content", ""),
            content_type=hit.payload.get("content_type", "text"),
        )
        for hit in hits
    ]


async def retrieve_arms(
    query: str, candidate_limit: int = 30, document_ids: list[uuid.UUID] | None = None
) -> tuple[list[_Hit], list[_Hit], list[_Scored]]:
    """Run both arms and fuse, returning ``(dense, sparse, fused)``.

    The Stage 5 graph needs the per-arm hits alongside the fused list so its
    state can carry ``dense_results`` and ``sparse_results`` as PROJECT.md
    Section 4 specifies. This delegates to the same functions
    :func:`hybrid_search` uses, so the two paths cannot drift.
    """
    collection = await ensure_collection(dense_dimension())
    total_points = (await get_qdrant_client().count(collection, exact=True)).count
    if not total_points:
        return [], [], []

    dense = await dense_search(query, limit=candidate_limit, document_ids=document_ids)
    sparse = await sparse_search(query, limit=candidate_limit, document_ids=document_ids)
    fused = reciprocal_rank_fusion([dense, sparse])
    return dense, sparse, fused


async def hybrid_search(
    query: str,
    limit: int = 10,
    document_ids: list[uuid.UUID] | None = None,
    candidate_limit: int = 30,
) -> HybridSearchResponse:
    """Run both arms, fuse with RRF, and return the top ``limit`` chunks."""
    collection = await ensure_collection(dense_dimension())
    total_points = (await get_qdrant_client().count(collection, exact=True)).count
    if not total_points:
        return HybridSearchResponse(
            query=query,
            results=[],
            dense_candidates=0,
            sparse_candidates=0,
            rrf_k=RRF_K,
            timings=ArmTimings(dense_ms=0.0, sparse_ms=0.0, fusion_ms=0.0),
        )

    started = time.perf_counter()
    dense = await dense_search(query, limit=candidate_limit, document_ids=document_ids)
    dense_ms = (time.perf_counter() - started) * 1000

    started = time.perf_counter()
    sparse = await sparse_search(query, limit=candidate_limit, document_ids=document_ids)
    sparse_ms = (time.perf_counter() - started) * 1000

    started = time.perf_counter()
    fused = reciprocal_rank_fusion([dense, sparse])
    fusion_ms = (time.perf_counter() - started) * 1000

    results = to_retrieved_chunks(fused, limit)

    logger.info(
        "hybrid_search '%s': dense=%d sparse=%d fused=%d in %.1fms",
        query,
        len(dense),
        len(sparse),
        len(fused),
        dense_ms + sparse_ms + fusion_ms,
    )

    return HybridSearchResponse(
        query=query,
        results=results,
        dense_candidates=len(dense),
        sparse_candidates=len(sparse),
        rrf_k=RRF_K,
        timings=ArmTimings(dense_ms=dense_ms, sparse_ms=sparse_ms, fusion_ms=fusion_ms),
    )
