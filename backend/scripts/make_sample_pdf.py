"""Minimal dependency-free PDF writer for the sample policy fixture.

Deliberately hand-rolls the PDF file format instead of adding reportlab: the
project's package manager is uv and Golden Rule 1 of WORKFLOW.md says not to
introduce libraries that aren't in PROJECT.md. Test fixtures are not worth a new
runtime dependency.

Only what the fixture needs: base-14 Helvetica text, word-wrapped paragraphs, and
grid tables with visible borders, flowed across multiple pages.
"""

from __future__ import annotations

from pathlib import Path

from .policy_content import APPENDIX, SECTIONS, TITLE

PAGE_W, PAGE_H = 595.0, 842.0  # A4 in points
MARGIN = 56.0
CONTENT_W = PAGE_W - 2 * MARGIN

FONT_REGULAR = "F1"
FONT_BOLD = "F2"


def _escape(text: str) -> str:
    return text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def _wrap(text: str, font_size: float, width: float) -> list[str]:
    """Greedy word wrap using an average glyph width for Helvetica (~0.5em)."""
    char_w = font_size * 0.5
    max_chars = max(8, int(width / char_w))
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) <= max_chars:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


class _Page:
    def __init__(self) -> None:
        self.ops: list[str] = []

    def text(self, x: float, y: float, value: str, size: float, bold: bool = False) -> None:
        font = FONT_BOLD if bold else FONT_REGULAR
        self.ops.append(f"BT /{font} {size:.1f} Tf {x:.2f} {y:.2f} Td ({_escape(value)}) Tj ET")

    def line(self, x1: float, y1: float, x2: float, y2: float, width: float = 0.5) -> None:
        self.ops.append(
            f"{width:.2f} w {x1:.2f} {y1:.2f} m {x2:.2f} {y2:.2f} l S"
        )

    def rect(self, x: float, y: float, w: float, h: float, width: float = 0.5) -> None:
        self.ops.append(
            f"{width:.2f} w {x:.2f} {y:.2f} {w:.2f} {h:.2f} re S"
        )

    def stream(self) -> bytes:
        return "\n".join(self.ops).encode("latin-1", "replace")


class _Doc:
    def __init__(self) -> None:
        self.pages: list[_Page] = []
        self.y = PAGE_H - MARGIN
        self.new_page()

    def new_page(self) -> None:
        self.pages.append(_Page())
        self.y = PAGE_H - MARGIN

    def ensure(self, needed: float) -> None:
        if self.y - needed < MARGIN:
            self.new_page()

    def heading(self, text: str, size: float = 13.0, gap_after: float = 8.0) -> None:
        lines = _wrap(text, size, CONTENT_W)
        for line in lines:
            self.ensure(size + 4)
            self.y -= size + 2
            self.pages[-1].text(MARGIN, self.y, line, size, bold=True)
        self.y -= gap_after

    def paragraph(self, text: str, size: float = 10.0, leading: float = 13.5) -> None:
        for line in _wrap(text, size, CONTENT_W):
            self.ensure(leading)
            self.y -= leading
            self.pages[-1].text(MARGIN, self.y, line, size)
        self.y -= 4

    def table(self, header: list[str], rows: list[list[str]]) -> None:
        size = 9.0
        leading = 12.0
        cols = len(header)
        col_w = CONTENT_W / cols

        def cell_lines(row: list[str]) -> list[list[str]]:
            """Wrapped lines for each cell in a row -> list[cell][line]."""
            return [_wrap(str(v), size, col_w - 8) for v in row]

        def row_height(cells: list[list[str]]) -> float:
            return leading * max(1, max(len(cell) for cell in cells))

        header_cells = cell_lines(header)
        body_cells = [cell_lines(r) for r in rows]

        self.ensure(leading * (len(rows) + 2) + 20)
        top = self.y

        def draw_row(cells: list[list[str]], y: float, bold: bool) -> float:
            for idx, cell in enumerate(cells):
                for r, line in enumerate(cell):
                    self.pages[-1].text(
                        MARGIN + idx * col_w + 4,
                        y - leading * (r + 1) + 3,
                        line,
                        size,
                        bold=bold,
                    )
            return y - row_height(cells)

        y = draw_row(header_cells, top, True)
        header_bottom = y
        for cells in body_cells:
            y = draw_row(cells, y, False)
        bottom = y

        # Borders: outer box, header rule, column rules, row rules
        page = self.pages[-1]
        page.rect(MARGIN, bottom, CONTENT_W, top - bottom, width=0.8)
        page.line(MARGIN, header_bottom, PAGE_W - MARGIN, header_bottom, width=0.8)
        for idx in range(1, cols):
            x = MARGIN + idx * col_w
            page.line(x, bottom, x, top, width=0.4)
        cursor = header_bottom
        for cells in body_cells:
            cursor -= row_height(cells)
            page.line(MARGIN, cursor, PAGE_W - MARGIN, cursor, width=0.4)

        self.y = bottom - 16


def _build() -> bytes:
    doc = _Doc()
    doc.heading(TITLE, size=17.0, gap_after=14)

    for heading, paragraphs, tables in SECTIONS:
        doc.heading(heading)
        for text in paragraphs:
            doc.paragraph(text)
        for caption, header, rows in tables:
            doc.paragraph(caption, size=9.5)
            doc.table(header, rows)
            doc.y -= 8

    doc.new_page()
    doc.heading(APPENDIX[0])
    for text in APPENDIX[1]:
        doc.paragraph(text)

    streams = [p.stream() for p in doc.pages]
    n_pages = len(doc.pages)

    objects: list[bytes] = []

    def add(obj: bytes) -> int:
        objects.append(obj)
        return len(objects)  # 1-based object number

    # Reserve object 1 (catalog) and 2 (pages) so kids can reference them.
    catalog_id = add(b"")
    pages_id = add(b"")
    font_regular_id = add(
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"
    )
    font_bold_id = add(
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>"
    )

    page_ids: list[int] = []
    for stream in streams:
        content_id = add(
            b"<< /Length "
            + str(len(stream)).encode()
            + b" >>\nstream\n"
            + stream
            + b"\nendstream"
        )
        page_ids.append(add(
            b"<< /Type /Page /Parent "
            + str(pages_id).encode()
            + b" 0 R /MediaBox [0 0 "
            + f"{PAGE_W:.0f} {PAGE_H:.0f}".encode()
            + b"] /Resources << /Font << /"
            + FONT_REGULAR.encode()
            + b" "
            + str(font_regular_id).encode()
            + b" 0 R /"
            + FONT_BOLD.encode()
            + b" "
            + str(font_bold_id).encode()
            + b" 0 R >> >> /Contents "
            + str(content_id).encode()
            + b" 0 R >>"
        ))

    objects[catalog_id - 1] = (
        b"<< /Type /Catalog /Pages " + str(pages_id).encode() + b" 0 R >>"
    )
    kids = b" ".join(str(pid).encode() + b" 0 R" for pid in page_ids)
    objects[pages_id - 1] = (
        b"<< /Type /Pages /Kids [" + kids + b"] /Count "
        + str(n_pages).encode()
        + b" >>"
    )

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets: list[int] = []
    for idx, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += str(idx).encode() + b" 0 obj\n" + body + b"\nendobj\n"

    xref_pos = len(out)
    count = len(objects) + 1
    out += b"xref\n0 " + str(count).encode() + b"\n"
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (
        b"trailer\n<< /Size "
        + str(count).encode()
        + b" /Root "
        + str(catalog_id).encode()
        + b" 0 R >>\nstartxref\n"
        + str(xref_pos).encode()
        + b"\n%%EOF\n"
    )
    return bytes(out)


def build_pdf(path: Path) -> Path:
    path.write_bytes(_build())
    return path
