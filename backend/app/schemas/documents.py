"""Request/response models for the document endpoints (PROJECT.md Section 6).

Typed all the way - no bare dicts, because the frontend depends on these shapes.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.core.config import PIPELINE_STATUSES, DocumentStatus


class DocumentUploadResponse(BaseModel):
    """PROJECT.md Section 6: upload -> { document_id, status }."""

    document_id: uuid.UUID
    status: DocumentStatus


class DocumentSummary(BaseModel):
    id: uuid.UUID
    filename: str
    file_type: str
    status: DocumentStatus
    created_at: datetime
    chunk_count: int = Field(
        description="Chunks written for this document (0 while still processing)"
    )


class DocumentListResponse(BaseModel):
    documents: list[DocumentSummary]
    total: int


class DocumentDetail(DocumentSummary):
    stage_index: int = Field(
        ge=-1,
        description=(
            "Position in the pipeline (0=uploading ... 5=ready). Drives the "
            "sidebar progress fill in PROJECT.md Section 11.2. -1 when failed."
        ),
    )
    stage_total: int = Field(description="Number of pipeline stages")

    @classmethod
    def build(
        cls,
        *,
        id: uuid.UUID,
        filename: str,
        file_type: str,
        status: DocumentStatus,
        created_at: datetime,
        chunk_count: int,
    ) -> DocumentDetail:
        try:
            index = PIPELINE_STATUSES.index(status)
        except ValueError:
            index = -1
        return cls(
            id=id,
            filename=filename,
            file_type=file_type,
            status=status,
            created_at=created_at,
            chunk_count=chunk_count,
            stage_index=index,
            stage_total=len(PIPELINE_STATUSES),
        )


class DocumentErrorResponse(BaseModel):
    detail: str


class BoundingBox(BaseModel):
    l: float
    t: float
    r: float
    b: float
    coord_origin: str = "BOTTOMLEFT"


class PageElement(BaseModel):
    text: str
    label: str
    bbox: BoundingBox | None = None


class DocumentPageResponse(BaseModel):
    """PROJECT.md Section 6: page text + bounding boxes for highlight."""

    document_id: uuid.UUID
    page_number: int
    text: str
    elements: list[PageElement] = Field(default_factory=list)

