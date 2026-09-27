"""Corpus statistics for BM25 IDF, built from the Qdrant payload.

``document_chunks`` has no content column (PROJECT.md Section 5 keeps chunk text
in the vector store, linked by ``qdrant_point_id``), so document frequencies are
counted by scrolling the collection's payload.

The result is cached in-process and invalidated whenever a document is indexed,
which keeps IDF exactly consistent with the corpus at the cost of a full scroll
after each ingest. That is acceptable at V1 scale; a larger corpus wants a
materialised term-frequency index instead.
"""

from __future__ import annotations

import asyncio

from app.core.config import get_settings
from app.core.logging import get_logger
from app.services.bm25 import CorpusStats, tokenize
from app.services.qdrant import get_qdrant_client

logger = get_logger(__name__)

_SCROLL_PAGE = 512

#: Guard against a pathological corpus.
_MAX_TERMS_TRACKED = 200_000


class CorpusStatsProvider:
    def __init__(self) -> None:
        self._stats: CorpusStats | None = None
        self._lock = asyncio.Lock()

    def invalidate(self) -> None:
        self._stats = None

    async def get(self) -> CorpusStats:
        async with self._lock:
            if self._stats is None:
                self._stats = await self._build()
            return self._stats

    async def _build(self) -> CorpusStats:
        client = get_qdrant_client()
        collection = get_settings().qdrant_collection

        doc_freq: dict[str, int] = {}
        total = 0
        offset = None

        while True:
            records, offset = await client.scroll(
                collection_name=collection,
                limit=_SCROLL_PAGE,
                offset=offset,
                with_payload=["content"],
                with_vectors=False,
            )
            for record in records:
                payload = record.payload or {}
                content = payload.get("content")
                if not content:
                    continue
                total += 1
                for term in set(tokenize(content)):
                    if len(doc_freq) >= _MAX_TERMS_TRACKED:
                        break
                    doc_freq[term] = doc_freq.get(term, 0) + 1

            if offset is None:
                break

        logger.info(
            "corpus stats: %d chunk(s), %d distinct term(s)", total, len(doc_freq)
        )
        return CorpusStats(total_chunks=total, doc_freq=doc_freq)


corpus_stats = CorpusStatsProvider()
