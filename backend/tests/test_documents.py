"""Document service helpers and the document API contract."""

from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException

from app.schemas.documents import DocumentDetail, DocumentUploadResponse
from app.services.documents import resolve_file_type


def test_resolve_file_type_is_case_insensitive() -> None:
    assert resolve_file_type("Policy.PDF") == "pdf"
    assert resolve_file_type("policy.pdf") == "pdf"


def test_resolve_file_type_rejects_unsupported() -> None:
    with pytest.raises(ValueError, match="unsupported file type"):
        resolve_file_type("malware.exe")


def test_rejects_filename_with_no_extension() -> None:
    with pytest.raises(ValueError):
        resolve_file_type("noextension")


def test_upload_response_is_typed_and_starts_in_uploading() -> None:
    response = DocumentUploadResponse(
        document_id=uuid.uuid4(), status="uploading"
    )
    assert response.status == "uploading"


def test_detail_maps_status_to_stage_index() -> None:
    detail = DocumentDetail.build(
        id=uuid.uuid4(),
        filename="p.pdf",
        file_type="pdf",
        status="indexing",
        created_at="2026-01-01T00:00:00Z",  # type: ignore[arg-type]
        chunk_count=13,
    )
    assert detail.stage_index == 4
    assert detail.stage_total == 6


def test_failed_status_reports_no_progress() -> None:
    detail = DocumentDetail.build(
        id=uuid.uuid4(),
        filename="p.pdf",
        file_type="pdf",
        status="failed",
        created_at="2026-01-01T00:00:00Z",  # type: ignore[arg-type]
        chunk_count=0,
    )
    assert detail.stage_index == -1


def test_detail_never_leaks_the_storage_path() -> None:
    detail = DocumentDetail.build(
        id=uuid.uuid4(),
        filename="p.pdf",
        file_type="pdf",
        status="ready",
        created_at="2026-01-01T00:00:00Z",  # type: ignore[arg-type]
        chunk_count=13,
    )
    assert "storage_path" not in detail.model_dump()


def test_upload_endpoint_is_registered_with_the_expected_contract() -> None:
    from app.main import app

    paths = app.openapi()["paths"]
    assert "/api/v1/documents" in paths
    assert "post" in paths["/api/v1/documents"]
    assert "/api/v1/documents/{document_id}" in paths
    assert "/api/v1/documents/{document_id}/pages/{page}" in paths
    assert "/health" in paths


def test_upload_route_does_not_exist_without_a_file() -> None:
    """multipart is required, so FastAPI must advertise the file part."""
    from app.main import app

    body = app.openapi()["paths"]["/api/v1/documents"]["post"]["requestBody"]
    assert "multipart/form-data" in body["content"]


def test_unsupported_upload_extension_is_rejected() -> None:
    with pytest.raises(ValueError, match="unsupported file type"):
        resolve_file_type("x.txt")


def test_error_responses_are_typed_models() -> None:
    """Handlers must not return bare dicts for errors."""
    from app.schemas.documents import DocumentErrorResponse

    assert DocumentErrorResponse(detail="unsupported file type").detail


def test_http_exception_is_the_route_level_error_type() -> None:
    """Route handlers signal client errors with HTTPException, not bare dicts."""
    assert issubclass(HTTPException, Exception)


def test_document_page_response_schema() -> None:
    from app.schemas.documents import BoundingBox, DocumentPageResponse, PageElement

    bbox = BoundingBox(l=10.5, t=20.5, r=100.0, b=40.0, coord_origin="BOTTOMLEFT")
    el = PageElement(text="Sample text", label="text", bbox=bbox)
    doc_id = uuid.uuid4()
    resp = DocumentPageResponse(
        document_id=doc_id,
        page_number=1,
        text="Sample text",
        elements=[el],
    )
    data = resp.model_dump()
    assert data["document_id"] == doc_id
    assert data["page_number"] == 1
    assert data["text"] == "Sample text"
    assert len(data["elements"]) == 1
    assert data["elements"][0]["bbox"]["l"] == 10.5


@pytest.mark.asyncio
async def test_get_document_page_raises_on_missing_document() -> None:
    from unittest.mock import AsyncMock

    from app.services.documents import DocumentNotFoundError, get_document_page

    mock_session = AsyncMock()
    mock_session.scalar.return_value = None

    with pytest.raises(DocumentNotFoundError, match="not found"):
        await get_document_page(mock_session, uuid.uuid4(), 1)


@pytest.mark.asyncio
async def test_get_document_page_reads_cached_json(
    tmp_path: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json
    from pathlib import Path
    from unittest.mock import AsyncMock

    from app.core.config import DocumentStatus
    from app.db.models import Document
    from app.services.documents import get_document_page

    doc_id = uuid.uuid4()
    mock_doc = Document(
        id=doc_id,
        filename="policy.pdf",
        file_type="pdf",
        storage_path=str(Path(str(tmp_path)) / f"{doc_id}.pdf"),
        status=DocumentStatus.READY.value,
    )
    mock_session = AsyncMock()
    mock_session.scalar.return_value = mock_doc

    monkeypatch.setattr("app.services.documents.storage_root", lambda: Path(str(tmp_path)))

    cache_file = Path(str(tmp_path)) / f"{doc_id}.pages.json"
    cache_file.write_text(
        json.dumps({
            "1": {
                "page_number": 1,
                "text": "Page one text content.",
                "elements": [
                    {
                        "text": "Page one text content.",
                        "label": "text",
                        "bbox": {
                            "l": 10.0,
                            "t": 20.0,
                            "r": 30.0,
                            "b": 40.0,
                            "coord_origin": "BOTTOMLEFT",
                        },
                    }
                ],
            }
        }),
        encoding="utf-8",
    )

    page_res = await get_document_page(mock_session, doc_id, 1)
    assert page_res.document_id == doc_id
    assert page_res.page_number == 1
    assert page_res.text == "Page one text content."
    assert len(page_res.elements) == 1
    assert page_res.elements[0].bbox is not None
    assert page_res.elements[0].bbox.l == 10.0


@pytest.mark.asyncio
async def test_get_document_page_raises_on_missing_page(
    tmp_path: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json
    from pathlib import Path
    from unittest.mock import AsyncMock

    from app.core.config import DocumentStatus
    from app.db.models import Document
    from app.services.documents import PageNotFoundError, get_document_page

    doc_id = uuid.uuid4()
    mock_doc = Document(
        id=doc_id,
        filename="policy.pdf",
        file_type="pdf",
        storage_path=str(Path(str(tmp_path)) / f"{doc_id}.pdf"),
        status=DocumentStatus.READY.value,
    )
    mock_session = AsyncMock()
    mock_session.scalar.return_value = mock_doc

    monkeypatch.setattr("app.services.documents.storage_root", lambda: Path(str(tmp_path)))
    cache_file = Path(str(tmp_path)) / f"{doc_id}.pages.json"
    cache_file.write_text(json.dumps({"1": {"page_number": 1, "text": "...", "elements": []}}), encoding="utf-8")

    with pytest.raises(PageNotFoundError, match="page 2 not found"):
        await get_document_page(mock_session, doc_id, 2)


def test_resolve_file_type_supports_spreadsheets() -> None:
    assert resolve_file_type("sheet.xlsx") == "xlsx"
    assert resolve_file_type("SHEET.XLSX") == "xlsx"
    assert resolve_file_type("allowances.csv") == "csv"
    assert resolve_file_type("ALLOWANCES.CSV") == "csv"


def test_spreadsheet_fixtures_parse_and_chunk_tables() -> None:
    from pathlib import Path
    from app.ingestion.parser import _convert_blocking, extract_document_pages
    from app.ingestion.chunker import chunk_document

    fixtures_dir = Path(__file__).resolve().parent.parent / "scripts" / "fixtures"
    csv_file = fixtures_dir / "sample_policy_allowances.csv"
    xlsx_file = fixtures_dir / "sample_policy_accommodations.xlsx"

    assert csv_file.exists(), f"Fixture {csv_file} missing"
    assert xlsx_file.exists(), f"Fixture {xlsx_file} missing"

    # CSV
    doc_csv = _convert_blocking(csv_file, ".csv")
    chunks_csv = chunk_document(doc_csv)
    pages_csv = extract_document_pages(doc_csv)

    assert len(chunks_csv) >= 1
    assert chunks_csv[0].content_type == "table"
    assert "|" in chunks_csv[0].content
    assert "Daily allowance" in chunks_csv[0].content
    assert len(pages_csv) >= 1
    assert "1" in pages_csv
    assert pages_csv["1"]["page_number"] == 1

    # XLSX
    doc_xlsx = _convert_blocking(xlsx_file, ".xlsx")
    chunks_xlsx = chunk_document(doc_xlsx)
    pages_xlsx = extract_document_pages(doc_xlsx)

    assert len(chunks_xlsx) >= 1
    assert chunks_xlsx[0].content_type == "table"
    assert "|" in chunks_xlsx[0].content
    assert "Nightly cap" in chunks_xlsx[0].content
    assert len(pages_xlsx) >= 1
    assert "1" in pages_xlsx
    assert pages_xlsx["1"]["page_number"] == 1



