"""Application settings, loaded from backend/.env per PROJECT.md Section 15."""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/core/config.py -> backend/
BACKEND_DIR = Path(__file__).resolve().parents[2]


class DocumentStatus(StrEnum):
    """Real ingestion pipeline states.

    PROJECT.md Section 11.2 specifies the sidebar cycles through
    Parsing -> Chunking -> Embedding -> Indexing -> Ready, and Section 6 starts
    an upload at ``uploading``. ``failed`` exists because WORKFLOW.md Golden Rule
    10 requires ingestion failures to be visible rather than swallowed.
    """

    UPLOADING = "uploading"
    PARSING = "parsing"
    CHUNKING = "chunking"
    EMBEDDING = "embedding"
    INDEXING = "indexing"
    READY = "ready"
    FAILED = "failed"


#: Ordered for progress reporting: index into this list is the sidebar fill %.
PIPELINE_STATUSES: tuple[DocumentStatus, ...] = (
    DocumentStatus.UPLOADING,
    DocumentStatus.PARSING,
    DocumentStatus.CHUNKING,
    DocumentStatus.EMBEDDING,
    DocumentStatus.INDEXING,
    DocumentStatus.READY,
)

#: Non-terminal states. A document found in one of these after a restart was
#: orphaned mid-ingestion and must be reconciled; ``ready`` is terminal and must
#: never be swept up by such a cleanup.
IN_PROGRESS_STATUSES: tuple[DocumentStatus, ...] = tuple(
    status for status in PIPELINE_STATUSES if status is not DocumentStatus.READY
)

SUPPORTED_EXTENSIONS: frozenset[str] = frozenset({".pdf", ".docx", ".xlsx", ".csv"})

FILE_TYPE_BY_EXTENSION: dict[str, str] = {
    ".pdf": "pdf",
    ".docx": "docx",
    ".xlsx": "xlsx",
    ".csv": "csv",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "RAG Policy"
    environment: str = "development"

    database_url: str
    qdrant_url: str = "http://localhost:6333"
    redis_url: str = "redis://localhost:6379/0"

    # PROJECT.md Section 15: "<set explicitly, don't hardcode a vendor in code".
    # No default on purpose - a missing value should fail loudly at boot.
    embedding_model: str = Field(min_length=1)

    qdrant_collection: str = "policy_chunks"
    redis_namespace: str = "rag_policy"

    storage_dir: Path = BACKEND_DIR / "storage"
    max_upload_bytes: int = 50 * 1024 * 1024

    # Dev-server auto-reload. It is convenient while writing code but it cancels
    # any in-flight ingestion task, because ingestion runs inside this process
    # (PROJECT.md Section 2 allows no worker queue in V1). Set RELOAD=false to
    # verify ingestion end to end.
    reload: bool = True

    @field_validator("database_url")
    @classmethod
    def _require_async_driver(cls, value: str) -> str:
        if "+psycopg" not in value and "+asyncpg" not in value:
            raise ValueError(
                "DATABASE_URL must use an async driver "
                "(postgresql+psycopg://...), per WORKFLOW.md Section 4"
            )
        return value

    @property
    def is_development(self) -> bool:
        return self.environment.lower() in {"development", "dev", "local"}


@lru_cache
def get_settings() -> Settings:
    return Settings()

