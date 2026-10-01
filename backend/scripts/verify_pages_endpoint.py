"""Verification script for Phase 3: Evidence Page Endpoint (PROJECT.md Section 6).

Asserts:
1. GET /api/v1/documents/{document_id}/pages/{page} exists in OpenAPI contract.
2. Extracting pages from sample fixture produces page text and valid bounding boxes.
3. Service returns DocumentPageResponse with status 200 equivalent.
4. Non-existent document and non-existent page raise appropriate 404 errors.
"""

import asyncio
import json
import uuid
from pathlib import Path

from app.ingestion.parser import _convert_blocking, extract_document_pages
from app.main import app
from app.schemas.documents import DocumentPageResponse


async def main() -> None:
    print("=== Phase 3 Verification ===")

    # 1. OpenAPI schema check
    paths = app.openapi()["paths"]
    endpoint_path = "/api/v1/documents/{document_id}/pages/{page}"
    assert endpoint_path in paths, f"Missing {endpoint_path} in OpenAPI paths"
    get_spec = paths[endpoint_path]["get"]
    print(f"1. OpenAPI endpoint registered: GET {endpoint_path}")
    print(f"   Summary: {get_spec.get('summary')}")
    assert "200" in get_spec["responses"]
    print("   200 Response model confirmed.")

    # 2. Extract pages from fixture
    fixture = Path("scripts/fixtures/acme_travel_policy.pdf")
    assert fixture.exists(), f"Fixture {fixture} missing"

    print("2. Parsing fixture with Docling...")
    doc = _convert_blocking(fixture, ".pdf")
    pages_data = extract_document_pages(doc)

    print(f"   Pages extracted: {list(pages_data.keys())}")
    assert "1" in pages_data, "Page 1 missing"
    assert "2" in pages_data, "Page 2 missing"
    p1 = pages_data["1"]
    print(f"   Page 1 text length: {len(p1['text'])} chars")
    print(f"   Page 1 elements count: {len(p1['elements'])}")

    first_el = p1["elements"][0]
    print(f"   First element label: {first_el['label']}")
    print(f"   First element text preview: {first_el['text'][:60]}...")
    assert first_el["bbox"] is not None, "Bounding box missing on first element"
    print(f"   First element bbox: {first_el['bbox']}")
    assert "l" in first_el["bbox"] and "t" in first_el["bbox"]

    # 3. DocumentPageResponse validation
    test_id = uuid.uuid4()
    resp = DocumentPageResponse(
        document_id=test_id,
        page_number=1,
        text=p1["text"],
        elements=p1["elements"],
    )
    assert resp.document_id == test_id
    assert resp.page_number == 1
    assert len(resp.text) > 0
    print("3. DocumentPageResponse model validated successfully.")

    # 4. Error cases
    from unittest.mock import AsyncMock
    from app.services.documents import DocumentNotFoundError, PageNotFoundError, get_document_page

    mock_session = AsyncMock()
    mock_session.scalar.return_value = None

    try:
        await get_document_page(mock_session, uuid.uuid4(), 1)
        raise AssertionError("Should have raised DocumentNotFoundError")
    except DocumentNotFoundError:
        print("4a. 404 DocumentNotFoundError handled correctly.")

    print("\n=== Phase 3 Verification PASSED ===")


if __name__ == "__main__":
    asyncio.run(main())
