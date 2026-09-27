"""Types shared by the reranker, the evidence gate and the graph (Stages 4-5).

Kept apart from :mod:`app.schemas.retrieval` because that module documents the
Stage 3 fusion debug surface; these types are what chat actually consumes.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.schemas.retrieval import RetrievedChunk


class RerankedChunk(BaseModel):
    """One candidate after cross-encoder scoring.

    ``score`` is the cross-encoder logit, which is what the evidence gate
    thresholds. ``rrf_score`` is carried through purely for the debug endpoint so
    reranking can be compared against Stage 3's fusion order.
    """

    chunk: RetrievedChunk
    score: float = Field(description="Cross-encoder relevance logit")
    rrf_score: float = Field(description="Stage 3 RRF score, for comparison only")
    rank: int = Field(ge=1, description="1-based position after reranking")


class EvidenceDecision(BaseModel):
    """The gate's verdict, with the numbers that produced it.

    Returned to the caller (and logged) so a wrong answer can be diagnosed
    without re-running the model.
    """

    sufficient: bool
    reason: str
    threshold: float = Field(description="Logit the best chunk had to clear")
    top_score: float | None = Field(default=None)
    supporting_chunks: int = Field(
        default=0, description="Chunks that cleared the threshold"
    )
    considered: int = Field(default=0, description="Chunks scored by the reranker")


class RerankResult(BaseModel):
    chunks: list[RerankedChunk]
    decision: EvidenceDecision
    elapsed_ms: float
