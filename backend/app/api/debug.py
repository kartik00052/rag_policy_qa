"""Internal debug surfaces.

Not part of the PROJECT.md Section 6 public API - the underscore prefix keeps it
out of the documented contract. It exists so fusion, generation and cost can be
exercised over HTTP during development, and so later phases can be built against
a real endpoint before the public API replaces it.

Two endpoints, deliberately separated:

``/search``
    Stage 3 hybrid retrieval only. No LLM, no generation, no reranking, so a
    retrieval problem can be isolated from a generation one.

``/ask``
    The full graph, run to completion, returning the *entire* final state. It
    exists because ``POST /api/v1/chat/stream`` reports only
    ``{answer, has_sufficient_evidence, citations}`` - the shape PROJECT.md
    Section 6 fixes - which makes three things unobservable from the client:
    how many provider calls a question actually cost, what each of them cost
    according to the provider's own timings, and which judge verdict rejected an
    attempt. Latency work and model selection both depend on those numbers, and
    neither belongs in the documented response contract.

    It runs the same ``run_answer`` graph, so nothing here is a parallel
    implementation that could drift from the request path.
"""

from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException, status

from app.rag.graph import run_answer
from app.schemas.retrieval import HybridSearchResponse, SearchRequest
from app.services.llm import ProviderStats
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


@router.post(
    "/ask",
    summary="Run the full graph and return the whole final state (debug only)",
)
async def debug_ask(payload: dict) -> dict:
    """One question through the real graph, with full telemetry.

    The response is assembled field by field rather than dumped from the state,
    because the state holds pydantic models and a queue that have no JSON form.
    Only the observability fields are returned alongside the answer, so this
    cannot drift into being a second, looser version of ``ChatFinal``.
    """
    query = str(payload.get("query") or "").strip()
    if not query:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="query is required",
        )
    document_ids = payload.get("document_ids") or None

    started = time.perf_counter()
    try:
        state = await run_answer(query, document_ids=document_ids)
    except Exception as exc:  # noqa: BLE001 - surfaced to the caller verbatim
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"graph failed: {exc}",
        ) from exc
    wall_ms = (time.perf_counter() - started) * 1000

    chunks = state.get("reranked_chunks") or []
    stats: list[ProviderStats] = list(state.get("generation_stats") or [])

    return {
        "query": query,
        "answer": state.get("answer") or "",
        "raw_answer": state.get("raw_answer"),
        "has_sufficient_evidence": bool(state.get("has_sufficient_evidence")),
        "outcome": state.get("outcome"),
        "attempts": int(state.get("attempts") or 0),
        "retried": int(state.get("attempts") or 0) > 1,
        "error": state.get("error"),
        "model": state.get("model"),
        "citations": [c.model_dump(mode="json") for c in (state.get("citations") or [])],
        "wall_ms": round(wall_ms, 1),
        # Time the graph did not spend inside the model: retrieval, fusion and
        # the cross-encoder. Reported next to the provider timings so a latency
        # investigation can tell "the model is slow" from "retrieval is slow".
        "graph_ms": round(wall_ms - sum(s.seconds("total_duration") for s in stats) * 1000, 1),
        "attempt_stats": [s.as_dict() for s in stats],
        "context": {
            "chunks": len(chunks),
            "prompt_chars": len(state.get("context") or ""),
            "scores": [round(c.score, 3) for c in chunks],
        },
    }
