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
from typing import Any

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

_convert_lock = asyncio.Lock()


def create_converter() -> DocumentConverter:
    """Create a DocumentConverter instance.

    A fresh converter instance is created per conversion to prevent native
    access violations (0xc0000005 in docling-parse C++ extension on Windows
    when a single converter instance is reused across multiple conversions).
    """
    return DocumentConverter(allowed_formats=list(FORMAT_BY_SUFFIX.values()))


def _convert_blocking(path: Path, suffix: str) -> DoclingDocument:
    del suffix  # format is inferred from the file; the converter allows all four
    converter = create_converter()
    result = converter.convert(source=path)

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


def extract_document_pages(document: DoclingDocument) -> dict[str, Any]:
    """Extract page text and bounding boxes for the evidence viewer (PROJECT.md Section 6)."""
    pages: dict[int, dict[str, Any]] = {}

    for item, _ in document.iterate_items():
        prov = getattr(item, "prov", None)
        p = (
            prov[0].page_no
            if prov and len(prov) > 0 and getattr(prov[0], "page_no", None)
            else 1
        )

        txt = getattr(item, "text", "") or ""
        label = getattr(item, "label", None)
        label_str = str(label.value) if hasattr(label, "value") else str(label or "")

        if not txt and "table" in label_str:
            try:
                txt = item.export_to_markdown(doc=document)
            except Exception:
                txt = ""

        if not txt.strip():
            continue

        bbox_dict = None
        if prov and len(prov) > 0 and getattr(prov[0], "bbox", None):
            b = prov[0].bbox
            try:
                bbox_dict = {
                    "l": round(float(b.l), 2),
                    "t": round(float(b.t), 2),
                    "r": round(float(b.r), 2),
                    "b": round(float(b.b), 2),
                    "coord_origin": str(getattr(b, "coord_origin", "BOTTOMLEFT")),
                }
            except Exception:
                bbox_dict = None

        if p not in pages:
            pages[p] = {"page_number": p, "text": "", "elements": []}

        pages[p]["elements"].append({
            "text": txt.strip(),
            "label": label_str,
            "bbox": bbox_dict,
        })

    if not pages:
        pages[1] = {"page_number": 1, "text": "", "elements": []}

    for p, data in pages.items():
        data["text"] = "\n\n".join(el["text"] for el in data["elements"])

    return {str(p): data for p, data in sorted(pages.items())}

