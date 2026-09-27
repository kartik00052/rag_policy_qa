"""Request/response models for hybrid retrieval (Stage 3).

RRF internals (per-list ranks, per-arm raw scores) are exposed deliberately:
this is the debug surface used to prove the fusion is correct, per WORKFLOW.md
Stage 3. Phase 2 chat will consume a trimmed projection of the same shapes.
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, description="Natural-language question")
    limit: int = Field(default=10, ge=1, le=50, description="Fused results to return")
    document_ids: list[uuid.UUID] | None = Field(
        default=None,
        description="Restrict to these documents. Omit to search the whole corpus.",
    )
    candidate_limit: int = Field(
        default=30, ge=1, le=100, description="Per-arm candidates before fusion"
    )


class RetrievedChunk(BaseModel):
    """One fused result with the provenance needed to audit the ranking."""

    point_id: str
    score: float = Field(description="RRF fused score")
    document_id: uuid.UUID
    filename: str
    chunk_index: int
    page_number: int | None
    section: str
    heading_path: list[str] = Field(
        default_factory=list,
        description="Ancestor headings from document root to the owning section",
    )
    content: str
    content_type: str
    dense_score: float | None = Field(
        default=None, description="Cosine score; None when not in the dense top-k"
    )
    sparse_score: float | None = Field(
        default=None, description="BM25 dot score; None when not in the sparse top-k"
    )
    dense_rank: int | None = None
    sparse_rank: int | None = None


class ArmTimings(BaseModel):
    dense_ms: float
    sparse_ms: float
    fusion_ms: float


class HybridSearchResponse(BaseModel):
    query: str
    results: list[RetrievedChunk]
    dense_candidates: int
    sparse_candidates: int
    rrf_k: int
    timings: ArmTimings
