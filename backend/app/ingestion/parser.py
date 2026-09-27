"""Docling parsing: file -> structured elements.

PROJECT.md Section 2 requires PDF/DOCX/XLSX/CSV to be parsed into structured
chunks with headings and tables preserved as distinct elements. This module owns
only the Docling call and the extension->format mapping; chunking lives in
``chunker.py``.

Docling is CPU-bound and loads layout models, so conversion runs in a worker
thread and is serialised behind a lock - the converter is a shared, heavyweight
object and concurrent conversions would spike memory.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from docling.datamodel.base_models import InputFormat
from docling.document_converter import DocumentConverter
from docling_core.types.doc.document import DoclingDocument

from app.core.logging import get_logger

logger = get_logger(__name__)

FORMAT_BY_SUFFIX: dict[str, InputFormat] = {
    ".pdf": InputFormat.PDF,
    ".docx": InputFormat.DOCX,
    ".xlsx": InputFormat.XLSX,
    ".csv": InputFormat.CSV,
}

_converter: DocumentConverter | None = None
_convert_lock = asyncio.Lock()


def get_converter() -> DocumentConverter:
    global _converter
    if _converter is None:
        _converter = DocumentConverter(
            allowed_formats=list(FORMAT_BY_SUFFIX.values())
        )
        logger.info("docling converter initialised")
    return _converter


def _convert_blocking(path: Path, suffix: str) -> DoclingDocument:
    del suffix  # format is inferred from the file; the converter allows all four
    result = get_converter().convert(source=path)

    # raises_on_error defaults to True, but check the status explicitly so a
    # partial/failed conversion can never be chunked into a "ready" document.
    status = getattr(result, "status", None)
    if status is not None and "SUCCESS" not in str(status) and "PARTIAL" not in str(status):
        raise RuntimeError(f"docling conversion status={status} for {path.name}")
    if status is not None and "PARTIAL" in str(status):
        logger.warning("docling reported PARTIAL_SUCCESS for %s", path.name)

    return result.document


async def parse_document(path: Path) -> DoclingDocument:
    """Parse a supported file into a structured Docling document."""
    suffix = path.suffix.lower()
    if suffix not in FORMAT_BY_SUFFIX:
        raise ValueError(f"unsupported file type: {suffix}")

    async with _convert_lock:
        return await asyncio.to_thread(_convert_blocking, path, suffix)
