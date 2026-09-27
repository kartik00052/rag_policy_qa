"""Internal debug surface for Stage 3 hybrid retrieval.

Not part of the PROJECT.md Section 6 public API - the underscore prefix keeps it
out of the documented contract. It exists so the fusion can be exercised over
HTTP during development, and so Phase 2 can be built against a real endpoint
before the chat/streaming route replaces it.

Deliberately no LLM, no generation, no reranking: Stage 3 only.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from app.schemas.retrieval import HybridSearchResponse, SearchRequest
from app.services.retrieval import hybrid_search

router = APIRouter(prefix="/api/v1/_debug", tags=["debug"])


@router.post(
    "/search",
    response_model=HybridSearchResponse,
    summary="Hybrid dense+BM25 search fused with RRF (debug only)",
)
async def debug_search(request: SearchRequest) -> HybridSearchResponse:
    try:
        return await hybrid_search(
            query=request.query,
            limit=request.limit,
            document_ids=request.document_ids,
            candidate_limit=request.candidate_limit,
        )
    except Exception as exc:  # noqa: BLE001 - surfaced to the caller verbatim
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"retrieval failed: {exc}",
        ) from exc
