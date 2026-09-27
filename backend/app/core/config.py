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

    # --- LLM provider (Stage 5) -------------------------------------------------
    # The provider is named in config and resolved through a registry in
    # app/services/llm.py, so no vendor is hardcoded in application logic. V1
    # ships one provider: Ollama.
    llm_provider: str = Field(min_length=1)
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = Field(min_length=1)
    llm_temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    llm_num_ctx: int = Field(default=4096, ge=512)
    llm_timeout_seconds: float = Field(default=180.0, gt=0.0)

    # --- Reranking + evidence gate (Stage 4) ------------------------------------
    # Stage 3 returns RRF-fused candidates. The cross-encoder reads each
    # (query, chunk) pair and emits a relevance logit; the graph then decides
    # whether the best of those logits clears evidence_min_score.
    reranker_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    rerank_candidate_limit: int = Field(default=30, ge=1, le=100)
    rerank_top_k: int = Field(default=8, ge=1, le=50)
    rerank_batch_size: int = Field(default=16, ge=1)

    # Evidence sufficiency (PROJECT.md Section 6, Stage 4).
    #
    # 0.0 is the cross-encoder's own decision boundary: the model emits a raw
    # logit, and logit 0 is sigmoid 0.5, i.e. "more likely relevant than not".
    # It is used rather than a value fitted to this corpus so the gate does not
    # silently encode the shape of the 13 chunks that happened to be ingested.
    #
    # Measured by scripts/calibrate_evidence.py against acme_travel_policy.pdf
    # (7 answerable / 9 absent questions): at 0.0, recall 6/7 and precision 8/9.
    # The two misses are documented rather than tuned away:
    #   * false negative - a nightly hotel cap asked in prose against a markdown
    #     table scores -1.89, because the encoder reads table text, not schema.
    #   * false positive - "how many sick leave days do *part-time* employees
    #     accrue" scores 8.36 against a full-time-only passage. A high score
    #     means "about that topic", never "answers your question"; only the
    #     generation step can catch that, hence the prompt's no-extrapolation
    #     rule.
    evidence_min_score: float = Field(
        default=0.0,
        description="Cross-encoder logit the best chunk must reach to count as evidence",
    )
    evidence_min_chunks: int = Field(
        default=1,
        ge=1,
        description="How many chunks must clear the threshold to allow generation",
    )

    # --- Single-tenant identity (Stage 6) ---------------------------------------
    # PROJECT.md Section 2: single-tenant, no per-department permissions, and
    # Section 14 (V2) owns the auth schema. conversations.user_id is NOT NULL,
    # so V1 needs one owning user row. It is created on first chat request.
    default_user_email: str = "dev@localhost"
    default_user_name: str = "Developer"

    #: Cap on the length of a persisted citation excerpt. Excerpts are substrings
    #: of chunk text, and chunk text can be long enough to bloat a message row.
    citation_excerpt_max_chars: int = Field(default=320, ge=40, le=2000)

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

