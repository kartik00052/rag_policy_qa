"""Chunking by section/heading (PROJECT.md Section 4).

Explicitly *not* fixed character windows. Chunks break at heading boundaries,
each carries its heading breadcrumb, and tables are emitted as standalone chunks
whose content is a real markdown table - never flattened into prose.

Heading depth is inferred from a leading outline number ("6" -> 1, "6.2" -> 2,
"6.2.1" -> 3), which is how policy documents actually nest. Documents without
numbering fall back to Docling's own item level, so the breadcrumb degrades
gracefully instead of pretending to a hierarchy that isn't there.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Final, Literal

from docling_core.types.doc.document import DoclingDocument

#: Sections larger than this get split on paragraph boundaries. Generous on
#: purpose: a chunk should normally be one whole section.
MAX_CHARS_PER_CHUNK: Final[int] = 2000

#: Below this, a short text run is merged into the following one so we do not
#: emit a chunk that is just a heading.
MIN_CHARS: Final[int] = 40

#: A single line this short is treated as a table caption rather than prose.
MAX_CAPTION_CHARS: Final[int] = 120

_OUTLINE_RE: Final[re.Pattern[str]] = re.compile(r"^(\d+(?:\.\d+)*)\.?\s")
_WS_RE: Final[re.Pattern[str]] = re.compile(r"\s+")
_HEADING_LABELS: Final[frozenset[str]] = frozenset({"section_header", "title"})
_TABLE_LABELS: Final[frozenset[str]] = frozenset({"table"})
_TEXT_LABELS: Final[frozenset[str]] = frozenset(
    {"text", "paragraph", "list_item", "code", "handwritten_text"}
)
#: Page furniture is dropped: it repeats on every page and pollutes BM25.
_SKIP_LABELS: Final[frozenset[str]] = frozenset(
    {"page_header", "page_footer", "footnote", "caption", "form"}
)

ContentType = Literal["text", "table"]


@dataclass(slots=True)
class Chunk:
    chunk_index: int
    content: str
    page_number: int | None
    section: str | None
    heading_path: list[str] = field(default_factory=list)
    content_type: ContentType = "text"


@dataclass(slots=True)
class _Buffer:
    """Accumulates prose belonging to the heading currently being read."""

    parts: list[str] = field(default_factory=list)
    page_number: int | None = None

    @property
    def text(self) -> str:
        return "\n\n".join(p for p in self.parts if p).strip()

    def add(self, text: str, page: int | None) -> None:
        if not text:
            return
        self.parts.append(text)
        # First page the section starts on is the most useful citation target.
        if self.page_number is None:
            self.page_number = page

    def take(self) -> str:
        value = self.text
        self.parts.clear()
        return value


def _normalise(text: str) -> str:
    return _WS_RE.sub(" ", text).strip().lower()


def _label_of(item: Any) -> str:
    label = getattr(item, "label", None)
    return str(label.value) if hasattr(label, "value") else str(label or "")


def _page_of(item: Any) -> int | None:
    """Docling's ``page_no`` is 1-based. Flow formats emit no provenance."""
    prov = getattr(item, "prov", None)
    if not prov:
        return None
    page = getattr(prov[0], "page_no", None)
    return int(page) if page else None


def _depth_of(text: str, fallback_level: int) -> int:
    match = _OUTLINE_RE.match(text.strip())
    if match:
        return match.group(1).count(".") + 1
    return max(1, fallback_level)


def _split_long(text: str, max_chars: int) -> list[str]:
    """Split oversized prose on paragraph boundaries."""
    if len(text) <= max_chars:
        return [text]

    pieces: list[str] = []
    current: list[str] = []
    length = 0

    for paragraph in text.split("\n\n"):
        if length + len(paragraph) + 2 > max_chars and current:
            pieces.append("\n\n".join(current))
            current, length = [], 0
        current.append(paragraph)
        length += len(paragraph) + 2

    if current:
        pieces.append("\n\n".join(current))
    return pieces


def _table_markdown(item: Any, document: DoclingDocument) -> str:
    """Markdown for a table. ``doc`` is required by the current docling-core."""
    return item.export_to_markdown(doc=document).strip()


def chunk_document(document: DoclingDocument) -> list[Chunk]:
    """Walk the parsed document in order and emit section-scoped chunks."""
    chunks: list[Chunk] = []
    heading_stack: list[tuple[int, str]] = []
    buffer = _Buffer()

    # Some backends emit a table's cell text as loose items *after* the table
    # item. This holds the last table's markdown so those duplicates are dropped,
    # and is cleared by the first item that is not part of that table.
    dedupe_against: str | None = None

    def path() -> list[str]:
        return [heading for _, heading in heading_stack]

    def section() -> str | None:
        return heading_stack[-1][1] if heading_stack else None

    def emit_text(text: str, page: int | None, *, force: bool = False) -> None:
        text = text.strip()
        if not text:
            return
        if not force and len(text) < MIN_CHARS:
            return
        for piece in _split_long(text, MAX_CHARS_PER_CHUNK):
            if not piece.strip():
                continue
            chunks.append(
                Chunk(
                    chunk_index=len(chunks),
                    content=piece.strip(),
                    page_number=page,
                    section=section(),
                    heading_path=path(),
                    content_type="text",
                )
            )

    def flush() -> None:
        emit_text(buffer.take(), buffer.page_number)

    for item, level in document.iterate_items():
        label = _label_of(item)
        page = _page_of(item)

        if label in _HEADING_LABELS:
            text = (getattr(item, "text", "") or "").strip()
            if not text:
                continue
            flush()
            dedupe_against = None
            depth = _depth_of(text, level)
            while heading_stack and heading_stack[-1][0] >= depth:
                heading_stack.pop()
            heading_stack.append((depth, text))
            continue

        if label in _SKIP_LABELS:
            continue

        if label in _TABLE_LABELS:
            markdown = _table_markdown(item, document)
            if not markdown:
                continue
            # Only a short single-line label is a caption. Anything longer is the
            # section's own prose and stays its own chunk, so prose is never
            # absorbed into a table chunk.
            pending = buffer.take()
            caption = ""
            if pending:
                if len(pending) <= MAX_CAPTION_CHARS and "\n\n" not in pending:
                    caption = pending
                else:
                    emit_text(pending, buffer.page_number, force=True)

            content = f"{caption}\n\n{markdown}" if caption else markdown
            chunks.append(
                Chunk(
                    chunk_index=len(chunks),
                    content=content,
                    page_number=page or buffer.page_number,
                    section=section(),
                    heading_path=path(),
                    content_type="table",
                )
            )
            dedupe_against = _normalise(markdown)
            continue

        if label in _TEXT_LABELS:
            text = (getattr(item, "text", "") or "").strip()
            if not text:
                continue
            if dedupe_against is not None:
                if _normalise(text) in dedupe_against:
                    continue  # cell text the table chunk already carries
                dedupe_against = None
            buffer.add(text, page)

    flush()

    # Re-index defensively so chunk_index is always 0..n-1 and contiguous.
    for position, chunk in enumerate(chunks):
        chunk.chunk_index = position

    return chunks
