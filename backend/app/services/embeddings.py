"""Dense embeddings via Sentence-Transformers.

Kept behind a small interface so the model stays swappable
(PROJECT.md Section 4: "one model, kept swappable behind an interface").

Encoding is CPU-bound and blocking, so it always runs in a worker thread and is
awaited - never called directly from the event loop
(WORKFLOW.md Section 4: no blocking calls in the request path).
"""

from __future__ import annotations

import asyncio

from sentence_transformers import SentenceTransformer

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_model: SentenceTransformer | None = None
_model_lock = asyncio.Lock()


def _dimension_of(model: SentenceTransformer) -> int:
    """Sentence-Transformers renamed this method; support both spellings."""
    getter = getattr(model, "get_embedding_dimension", None) or model.get_sentence_embedding_dimension
    return int(getter())


def get_model() -> SentenceTransformer:
    """Load the model once, synchronously (first call is the slow one)."""
    global _model
    if _model is None:
        name = get_settings().embedding_model
        logger.info("loading embedding model: %s", name)
        _model = SentenceTransformer(name, device="cpu")
        logger.info("embedding model ready (dim=%s)", _dimension_of(_model))
    return _model


def dense_dimension() -> int:
    return _dimension_of(get_model())


def _encode_blocking(texts: list[str]) -> list[list[float]]:
    model = get_model()
    vectors = model.encode(
        texts,
        convert_to_numpy=True,
        normalize_embeddings=True,  # cosine == dot product on unit vectors
        show_progress_bar=False,
        batch_size=32,
    )
    return [[float(x) for x in vec] for vec in vectors]


async def embed_documents(texts: list[str]) -> list[list[float]]:
    """Embed chunk texts. Order matches the input list."""
    if not texts:
        return []
    async with _model_lock:
        return await asyncio.to_thread(_encode_blocking, texts)


async def embed_query(text: str) -> list[float]:
    vectors = await embed_documents([text])
    return vectors[0]
