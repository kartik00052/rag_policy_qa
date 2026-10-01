"""FastAPI application factory (Stage 0 skeleton)."""

from __future__ import annotations

from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import chat, debug, documents, health
from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.db.session import dispose_engine
from app.schemas.health import ServiceInfo
from app.services import documents as documents_service
from app.services.llm import close_llm
from app.services.qdrant import close_qdrant

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    settings = get_settings()
    logger.info(
        "starting %s in %s (embedding model: %s)",
        settings.app_name,
        settings.environment,
        settings.embedding_model,
    )
    try:
        await documents_service.reconcile_interrupted_documents()
    except Exception:  # noqa: BLE001 - never block startup on cleanup
        logger.exception("interrupted-document reconciliation failed")
    yield
    logger.info("shutting down")
    await close_llm()
    await close_qdrant()
    await dispose_engine()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description=(
            "RAG Policy Assistant backend. Stages 0-6 of WORKFLOW.md: skeleton, "
            "data layer, document ingestion, hybrid retrieval, reranking with an "
            "evidence gate, LangGraph answer generation, and streaming chat."
        ),
        lifespan=lifespan,
    )

    # The Vite dev server runs on its own origin (5173) from the API (8000), so
    # every fetch and every EventSource is cross-origin. Without this the browser
    # blocks the response and the frontend fails with a CORS error that looks
    # like a backend outage. Origins are listed explicitly rather than using "*"
    # because allow_credentials=True is not compatible with a wildcard origin.
    # Production frontend requests go through the Vite reverse proxy or same
    # origin, so this list is dev-oriented by design.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:3000",
            "http://127.0.0.1:3000",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health.router)
    app.include_router(documents.router)
    app.include_router(chat.router)
    app.include_router(debug.router)

    @app.get("/", response_model=ServiceInfo, include_in_schema=False)
    async def root() -> ServiceInfo:
        return ServiceInfo(service=settings.app_name, version="0.1.0", health="/health")

    return app


app = create_app()
