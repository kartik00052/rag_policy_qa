"""Graph state for the RAG pipeline (PROJECT.md Section 4, Stage 5).

The first block is the documented contract and matches Section 4 field for
field. The second block is operational: it exists because Stage 6 streams tokens
while the graph is still running, which needs a side channel, and because
retrieval needs a document filter. Neither is persisted and neither is part of
the graph's conceptual shape.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any, TypedDict

from app.schemas.chat import Citation
from app.schemas.rag import EvidenceDecision, RerankedChunk
from app.schemas.retrieval import RetrievedChunk


class RAGState(TypedDict, total=False):
    """State threaded through retrieve -> rerank -> gate -> generate.

    ``total=False`` throughout: each node returns only the keys it owns, and
    LangGraph merges the partial updates.
    """

    # --- documented contract (PROJECT.md Section 4) ---
    query: str
    dense_results: list[RetrievedChunk]
    sparse_results: list[RetrievedChunk]
    reranked_chunks: list[RerankedChunk]
    context: str
    answer: str
    citations: list[Citation]
    has_sufficient_evidence: bool

    # --- operational, not persisted ---
    #: Restrict retrieval to these documents; None searches the whole corpus.
    document_ids: list[uuid.UUID] | None
    #: Fused RRF candidates, before reranking. Kept so the reranker can be
    #: compared against Stage 3's order without re-running retrieval.
    candidates: list[RetrievedChunk]
    #: Why the gate opened or closed, kept for the debug endpoint and logs.
    evidence: EvidenceDecision
    #: Queue the generation node pushes answer pieces into, so Stage 6 can
    #: forward them as they arrive instead of waiting for the whole answer.
    token_sink: asyncio.Queue
    #: The model's output before citation markers were stripped. Kept because
    #: markers are what citations are resolved from.
    raw_answer: str
    #: Anything that must be reported rather than raised: see the error contract
    #: in app/rag/graph.py.
    error: Any
