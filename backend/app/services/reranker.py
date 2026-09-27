"""Stage 4: cross-encoder reranking of fused candidates.

Stage 3's RRF fusion orders chunks by *rank agreement* between the dense and
sparse arms. That is a useful recall signal but a weak precision signal: a chunk
both arms mildly like can outrank a chunk one arm considers an exact match,
because cosine similarity and BM25 are never comparable to each other.

A cross-encoder reads each ``(query, chunk)`` pair *jointly*, which is what
actually distinguishes "this passage answers the question" from "this passage is
about the same topic". Its logit is the signal the evidence gate thresholds and
the number the LLM's citation ``relevance`` is taken from, so the gate, the
citations and the answer all trace back to one measurement.

Scoring is CPU-bound and blocking, so it runs in a worker thread and is awaited -
never called directly from the event loop (WORKFLOW.md Section 4).
"""

from __future__ import annotations

import asyncio
import time

from sentence_transformers import CrossEncoder

from app.core.config import get_settings
from app.core.logging import get_logger
from app.schemas.rag import RerankedChunk
from app.schemas.retrieval import RetrievedChunk

logger = get_logger(__name__)

_model: CrossEncoder | None = None
_model_lock = asyncio.Lock()


def get_model() -> CrossEncoder:
    """Load the cross-encoder once, synchronously (first call is the slow one)."""
    global _model
    if _model is None:
        name = get_settings().reranker_model
        logger.info("loading cross-encoder: %s", name)
        _model = CrossEncoder(name, device="cpu")
        logger.info("cross-encoder ready: %s", name)
    return _model


def _passage_text(chunk: RetrievedChunk) -> str:
    """Chunk text as the cross-encoder should read it.

    The heading path is prepended because a passage is often ambiguous on its
    own ("Employees may request..." means something different under
    "Leave > Accrual" than under "Leave > Carry-over limits"). The encoder was
    trained on plain passages, so the heading is joined as a leading line rather
    than interleaved.
    """
    if chunk.heading_path:
        return " > ".join(chunk.heading_path) + "\n\n" + chunk.content
    return chunk.content


def _score_blocking(query: str, passages: list[str], batch_size: int) -> list[float]:
    model = get_model()
    scores = model.predict(
        [(query, passage) for passage in passages],
        batch_size=batch_size,
        show_progress_bar=False,
        convert_to_numpy=True,
    )
    return [float(score) for score in scores]


async def rerank(
    query: str, candidates: list[RetrievedChunk], top_k: int | None = None
) -> list[RerankedChunk]:
    """Score every candidate, then keep the best ``top_k``.

    Every candidate is scored (the cross-encoder has to see all of them to rank
    them); only the top ``top_k`` are returned. Because the result is ordered by
    descending score, gating on this list is equivalent to gating on the full
    candidate set for a "did anything clear the bar" rule - see
    :mod:`app.services.evidence`.
    """
    settings = get_settings()
    limit = top_k if top_k is not None else settings.rerank_top_k
    if not candidates:
        return []

    passages = [_passage_text(chunk) for chunk in candidates]
    started = time.perf_counter()
    async with _model_lock:
        scores = await asyncio.to_thread(
            _score_blocking, query, passages, settings.rerank_batch_size
        )
    elapsed_ms = (time.perf_counter() - started) * 1000

    ranked = [
        (chunk, score) for chunk, score in zip(candidates, scores, strict=True)
    ]
    # Ties break on the Stage 3 order (rrf_score) so reranking is deterministic.
    ranked.sort(key=lambda item: (-item[1], -item[0].score))

    kept = [
        RerankedChunk(
            chunk=chunk, score=score, rrf_score=chunk.score, rank=position
        )
        for position, (chunk, score) in enumerate(ranked[:limit], start=1)
    ]

    logger.info(
        "rerank '%s': %d candidate(s) -> top %d, best=%.3f in %.0fms",
        query,
        len(candidates),
        len(kept),
        kept[0].score if kept else float("nan"),
        elapsed_ms,
    )
    return kept
