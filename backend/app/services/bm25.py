"""BM25 as Qdrant sparse vectors (PROJECT.md Section 4).

Scoring is a dot product between two sparse vectors, so BM25 is split across the
two sides:

* **index time** - each chunk stores the saturated, length-normalised term
  frequency ``w(t,d) = tf*(k1+1) / (tf + k1*(1 - b + b*|d|/avgdl))``
* **query time** - the query vector carries ``IDF(t)`` per query term

Their dot product is exactly the BM25 score for that query against that chunk.

Two deliberate V1 approximations, both documented rather than hidden:

1. ``avgdl`` is the mean token length of the chunks *within the document being
   indexed*, not the whole corpus. That keeps indexing self-contained (no global
   stats to keep in sync) at the cost of slightly different length normalisation
   for corpora spanning many documents.
2. Term -> index mapping uses a 32-bit BLAKE2b digest rather than a persisted
   vocabulary, so it is stateless across restarts. With V1-sized corpora the
   collision rate is negligible; a corpus large enough to care would want a real
   vocabulary table.

``IDF(t)`` uses the BM25+ form ``ln(1 + (N - df + 0.5) / (df + 0.5))``, which
stays positive for terms appearing in every document, so common terms damp the
score instead of inverting it.

Qdrant sparse indices are uint32, hence the digest size.
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass, field
from typing import Final

from qdrant_client.http.models import SparseVector

from app.core.logging import get_logger

logger = get_logger(__name__)

K1: Final[float] = 1.2
B: Final[float] = 0.75

_TOKEN_RE: Final[re.Pattern[str]] = re.compile(r"[a-z0-9]+(?:\.[0-9]+)*")

#: Qdrant sparse vector indices are unsigned 32-bit.
_MAX_INDEX: Final[int] = (1 << 32) - 1

#: Ceiling on terms per chunk, to stop a pathological document producing a huge
#: sparse vector. Longest/most frequent terms win.
_MAX_TERMS_PER_DOC: Final[int] = 2048


def tokenize(text: str) -> list[str]:
    """Lowercase word tokens, keeping dotted forms like ``6.2`` intact.

    PROJECT.md calls out exact terms such as "Section 6.2" as the reason sparse
    retrieval exists, so ``6.2`` must survive as one token rather than splitting
    into ``6`` and ``2``.
    """
    return _TOKEN_RE.findall(text.lower())


def term_index(term: str) -> int:
    digest = hashlib.blake2b(term.encode("utf-8"), digest_size=4).digest()
    return int.from_bytes(digest, "big") & _MAX_INDEX


@dataclass(frozen=True, slots=True)
class CorpusStats:
    """Document frequencies over the indexed chunk corpus."""

    total_chunks: int
    doc_freq: dict[str, int] = field(default_factory=dict)

    def idf(self, term: str) -> float:
        n = self.total_chunks
        if n <= 0:
            return 0.0
        df = self.doc_freq.get(term, 0)
        return math.log(1.0 + (n - df + 0.5) / (df + 0.5))


def bm25_doc_weights(tokens: list[str], avgdl: float) -> dict[str, float]:
    """Index-time term weights for one chunk."""
    if not tokens:
        return {}

    counts: dict[str, int] = {}
    for token in tokens:
        counts[token] = counts.get(token, 0) + 1

    doc_len = float(len(tokens))
    # avgdl of a single-chunk document collapses to that chunk's own length,
    # which makes the length-normalisation term exactly 1.
    avg = avgdl if avgdl > 0 else doc_len
    norm = K1 * (1.0 - B + B * (doc_len / avg))

    if len(counts) > _MAX_TERMS_PER_DOC:
        counts = dict(
            sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[
                :_MAX_TERMS_PER_DOC
            ]
        )

    weights: dict[str, float] = {}
    for term, tf in counts.items():
        weights[term] = (tf * (K1 + 1.0)) / (tf + norm)
    return weights


def sparse_vector_from_weights(weights: dict[str, float]) -> SparseVector:
    """Collapse a term->weight map into Qdrant's parallel indices/values arrays."""
    if not weights:
        return SparseVector(indices=[], values=[])

    merged: dict[int, float] = {}
    for term, weight in weights.items():
        idx = term_index(term)
        # Hash collision: keep the stronger weight rather than dropping the term.
        merged[idx] = max(merged.get(idx, 0.0), weight)

    ordered = sorted(merged.items())
    return SparseVector(
        indices=[idx for idx, _ in ordered],
        values=[value for _, value in ordered],
    )


def chunk_sparse_vector(content: str, avgdl: float) -> SparseVector:
    return sparse_vector_from_weights(bm25_doc_weights(tokenize(content), avgdl))


def query_sparse_vector(query: str, stats: CorpusStats) -> SparseVector:
    """Query-time vector carrying IDF per query term."""
    terms = tokenize(query)
    if not terms:
        return SparseVector(indices=[], values=[])

    # A repeated query term should not multiply its own weight.
    weights = {term: stats.idf(term) for term in dict.fromkeys(terms)}
    return sparse_vector_from_weights(weights)
