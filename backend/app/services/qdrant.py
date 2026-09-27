"""Qdrant client and collection management.

One collection holds two *named* vectors per PROJECT.md Section 4:
``dense`` (Sentence-Transformers, cosine) and ``sparse`` (BM25 weights), so
hybrid search can query each side independently and fuse in the service layer.
"""

from __future__ import annotations

import uuid

from qdrant_client import AsyncQdrantClient
from qdrant_client.http.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    PayloadSchemaType,
    SparseIndexParams,
    SparseVectorParams,
    VectorParams,
)

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

DENSE_VECTOR = "dense"
SPARSE_VECTOR = "sparse"

_client: AsyncQdrantClient | None = None


def get_qdrant_client() -> AsyncQdrantClient:
    global _client
    if _client is None:
        _client = AsyncQdrantClient(url=get_settings().qdrant_url)
    return _client


async def close_qdrant() -> None:
    global _client
    if _client is not None:
        await _client.close()
    _client = None


async def ensure_collection(dense_dim: int) -> str:
    """Create the chunk collection if missing. Safe to call on every boot.

    The dense size is derived from the loaded embedding model rather than
    hardcoded, so swapping EMBEDDING_MODEL only needs a new collection.
    """
    client = get_qdrant_client()
    collection = get_settings().qdrant_collection

    if not await client.collection_exists(collection):
        await client.create_collection(
            collection_name=collection,
            vectors_config={
                DENSE_VECTOR: VectorParams(size=dense_dim, distance=Distance.COSINE)
            },
            sparse_vectors_config={
                SPARSE_VECTOR: SparseVectorParams(index=SparseIndexParams())
            },
        )

    # Keyword index so document-scoped filters stay fast.
    for field in ("document_id", "filename"):
        try:
            await client.create_payload_index(
                collection_name=collection,
                field_name=field,
                field_schema=PayloadSchemaType.KEYWORD,
            )
        except Exception:  # noqa: BLE001 - index may already exist
            pass

    return collection


async def delete_document_points(document_id: uuid.UUID | str) -> int:
    """Delete every chunk point belonging to one document. Returns the count.

    Scoped by a payload filter on ``document_id`` rather than by a list of known
    point ids, because the caller that needs this most - startup reconciliation -
    is recovering from a process that died and no longer has the id list in
    memory. ``document_id`` is keyword-indexed by :func:`ensure_collection`, so
    the filter is an indexed lookup rather than a full scan.

    Returns 0 when the collection does not exist yet, so this is safe to call on
    a fresh install.
    """
    client = get_qdrant_client()
    collection = get_settings().qdrant_collection
    if not await client.collection_exists(collection):
        return 0

    scope = Filter(
        must=[
            FieldCondition(key="document_id", match=MatchValue(value=str(document_id)))
        ]
    )
    # Count first: Qdrant's delete returns a status, not a deleted-row count, so
    # the only honest way to report "how many orphans did this clear" is to ask.
    removed = (await client.count(collection, count_filter=scope, exact=True)).count
    if not removed:
        return 0

    await client.delete(
        collection_name=collection,
        points_selector=scope,
        wait=True,
    )
    logger.info("deleted %d qdrant point(s) for document %s", removed, document_id)
    return removed
