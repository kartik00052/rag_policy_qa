"""Document endpoints (PROJECT.md Section 6).

Handlers stay thin: validation and file IO live in the service layer, and all
ingestion work happens in a background task so the response returns immediately
with the real pipeline stages observable afterwards.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Path as PathParam, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_session
from app.core.config import SUPPORTED_EXTENSIONS, DocumentStatus
from app.core.logging import get_logger
from app.schemas.documents import (
    DocumentDetail,
    DocumentErrorResponse,
    DocumentListResponse,
    DocumentPageResponse,
    DocumentSummary,
    DocumentUploadResponse,
)
from app.services import documents as document_service

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/documents", tags=["documents"])


@router.post(
    "",
    response_model=DocumentUploadResponse,
    status_code=status.HTTP_201_CREATED,
    responses={400: {"model": DocumentErrorResponse}},
    summary="Upload a policy document",
)
async def upload_document(
    file: UploadFile = File(..., description="PDF, DOCX, XLSX or CSV"),
    session: AsyncSession = Depends(get_session),
) -> DocumentUploadResponse:
    filename = file.filename or ""
    suffix = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if suffix not in SUPPORTED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"unsupported file type '{suffix or filename}'. "
                f"Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}"
            ),
        )

    try:
        file_type = document_service.resolve_file_type(filename)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    document_id = uuid.uuid4()
    try:
        stored_path = await document_service.save_upload(file, document_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    document = await document_service.create_document(
        session, filename=filename, file_type=file_type, storage_path=stored_path
    )
    logger.info(
        "upload accepted: %s (%s) -> %s", filename, file_type, document.id
    )

    # Commit before firing the background task so the row is visible to the
    # task's own session_scope() connection. create_document() calls
    # session.flush() which writes the row but does NOT commit it — the
    # request-scoped session only commits when get_session()'s __aexit__
    # runs after the handler returns. asyncio.create_task() schedules the
    # ingestion coroutine immediately, and it can reach its first await
    # (opening session_scope() and querying Document.id) before the request
    # session's implicit commit, causing 'document vanished mid-ingestion'.
    # Explicitly committing here, before create_task, closes that window
    # entirely: once this line returns the row is durable in the DB and
    # visible to every connection, regardless of event-loop scheduling.
    await session.commit()

    document_service.start_ingestion(document.id, stored_path)

    return DocumentUploadResponse(
        document_id=document.id, status=DocumentStatus.UPLOADING
    )


@router.get("", response_model=DocumentListResponse, summary="List documents")
async def list_documents(
    session: AsyncSession = Depends(get_session),
) -> DocumentListResponse:
    rows = await document_service.list_documents(session)
    summaries = [
        DocumentSummary(
            id=document.id,
            filename=document.filename,
            file_type=document.file_type,
            status=DocumentStatus(document.status),
            created_at=document.created_at,
            chunk_count=count,
        )
        for document, count in rows
    ]
    return DocumentListResponse(documents=summaries, total=len(summaries))


@router.get(
    "/{document_id}",
    response_model=DocumentDetail,
    responses={404: {"model": DocumentErrorResponse}},
    summary="Document detail and ingestion status",
)
async def get_document(
    document_id: uuid.UUID = PathParam(..., description="Document UUID"),
    session: AsyncSession = Depends(get_session),
) -> DocumentDetail:
    found = await document_service.get_document(session, document_id)
    if found is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"document {document_id} not found",
        )
    document, count = found
    return DocumentDetail.build(
        id=document.id,
        filename=document.filename,
        file_type=document.file_type,
        status=DocumentStatus(document.status),
        created_at=document.created_at,
        chunk_count=count,
    )


@router.get(
    "/{document_id}/pages/{page}",
    response_model=DocumentPageResponse,
    responses={
        404: {"model": DocumentErrorResponse},
        409: {"model": DocumentErrorResponse},
    },
    summary="Get document page text and bounding boxes",
)
async def get_document_page(
    document_id: uuid.UUID = PathParam(..., description="Document UUID"),
    page: int = PathParam(..., ge=1, description="1-based page number"),
    session: AsyncSession = Depends(get_session),
) -> DocumentPageResponse:
    try:
        return await document_service.get_document_page(session, document_id, page)
    except document_service.DocumentNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except document_service.PageNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except document_service.DocumentNotReadyError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc

