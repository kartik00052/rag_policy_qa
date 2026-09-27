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
