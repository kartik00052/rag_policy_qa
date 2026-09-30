"""Regression test for the flush-vs-commit race condition.

ROOT CAUSE (phase 8 diagnostic report):
    upload_document() called create_document() which used session.flush() to
    write the Document row, then called start_ingestion() which fired
    asyncio.create_task(_run_ingestion(document_id, ...)). The background task
    opened its own session_scope() and called set_status(), which queries the
    row via a SELECT. If the task first awaited before the request-scoped
    session committed (get_session().__aexit__ commits after the handler
    returns), the SELECT returned None and raised:
        RuntimeError("document ... vanished mid-ingestion")

FIX:
    await session.commit() in upload_document(), immediately after
    create_document() and before start_ingestion(), so the row is durable
    and visible to every connection before any background work that depends
    on it is scheduled.

TEST STRATEGY:
    Tests 1 and 2 use raw sqlite3 (stdlib, no extra deps) to prove the MVCC
    isolation invariant that the race relied on:
      - An uncommitted write in connection A is invisible to connection B.
      - After A commits, connection B can see it.
    This directly mirrors the request session (A) vs the background task's
    session_scope() (B).

    Test 3 verifies the handler call sequence via mocks: records whether
    session.commit() was called before start_ingestion fires.
"""

from __future__ import annotations

import io
import sqlite3
import tempfile
import uuid as uuid_mod
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from app.db.models import Document
from app.core.config import DocumentStatus


# ---------------------------------------------------------------------------
# Tests 1 & 2: MVCC isolation using stdlib sqlite3 (no extra deps required)
# ---------------------------------------------------------------------------

def _create_db(path: str) -> None:
    conn = sqlite3.connect(path, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        "CREATE TABLE documents (id TEXT PRIMARY KEY, status TEXT NOT NULL)"
    )
    conn.close()


def test_flush_equivalent_does_not_make_row_visible_to_independent_connection() -> None:
    """Documents the bug condition: uncommitted write is invisible cross-connection.

    sqlite3 WAL mode mirrors the MVCC isolation used by Postgres. An uncommitted
    INSERT in connection A (= the request session after flush()) is not visible
    to connection B (= the background task's session_scope()). This is the
    isolation invariant the 'vanished mid-ingestion' race depended on.
    """
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        _create_db(db_path)

        doc_id = str(uuid_mod.uuid4())

        writer = sqlite3.connect(db_path, isolation_level=None)
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("BEGIN")
        writer.execute(
            "INSERT INTO documents (id, status) VALUES (?, ?)", (doc_id, "uploading")
        )
        # Row is flushed but transaction is NOT committed -- mirrors session.flush().

        reader = sqlite3.connect(db_path, isolation_level=None)
        reader.execute("PRAGMA journal_mode=WAL")
        reader.execute("BEGIN")
        row = reader.execute(
            "SELECT id FROM documents WHERE id = ?", (doc_id,)
        ).fetchone()
        reader.execute("ROLLBACK")
        reader.close()

        writer.execute("ROLLBACK")
        writer.close()

        assert row is None, (
            "An uncommitted INSERT was visible across connections -- "
            "this isolation invariant must hold for the race to have existed."
        )
    finally:
        Path(db_path).unlink(missing_ok=True)


def test_commit_makes_row_visible_to_independent_connection() -> None:
    """Proves the fix is sufficient: after commit(), any new connection sees the row.

    After the explicit await session.commit() in upload_document(), the
    background task's session_scope() will always find the Document row
    regardless of event-loop scheduling.
    """
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        _create_db(db_path)

        doc_id = str(uuid_mod.uuid4())

        writer = sqlite3.connect(db_path, isolation_level=None)
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("BEGIN")
        writer.execute(
            "INSERT INTO documents (id, status) VALUES (?, ?)", (doc_id, "uploading")
        )
        writer.execute("COMMIT")  # The fix: commit before start_ingestion.
        writer.close()

        reader = sqlite3.connect(db_path, isolation_level=None)
        reader.execute("PRAGMA journal_mode=WAL")
        row = reader.execute(
            "SELECT id, status FROM documents WHERE id = ?", (doc_id,)
        ).fetchone()
        reader.close()

        assert row is not None, "After commit(), the row must be visible to any new connection"
        assert row[0] == doc_id
        assert row[1] == "uploading"
    finally:
        Path(db_path).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Test 3: Handler call ordering -- commit happens before start_ingestion
# ---------------------------------------------------------------------------

async def test_upload_handler_commits_before_start_ingestion() -> None:
    """Verifies the fix is present at the call site in upload_document().

    A regression (removing the explicit commit) would record committed=False
    when start_ingestion fires, and the final assertion catches that.
    """
    commit_before_ingestion: list[bool] = []
    committed = False

    doc_id = uuid_mod.uuid4()
    saved_path = Path("/tmp/phase8_vendor_policy.pdf")

    class MockSession:
        async def commit(self) -> None:
            nonlocal committed
            committed = True

        async def rollback(self) -> None:
            pass

    class _FakeDoc:
        id = doc_id

    async def fake_create_document(session: Any, **kwargs: Any) -> _FakeDoc:
        return _FakeDoc()

    def spy_start_ingestion(document_id: uuid_mod.UUID, path: Path) -> None:
        commit_before_ingestion.append(committed)

    with (
        patch("app.api.documents.document_service.resolve_file_type", return_value="pdf"),
        patch("app.api.documents.document_service.save_upload", return_value=saved_path),
        patch("app.api.documents.document_service.create_document", side_effect=fake_create_document),
        patch("app.api.documents.document_service.start_ingestion", side_effect=spy_start_ingestion),
    ):
        from app.api.documents import upload_document
        from fastapi import UploadFile

        fake_file = UploadFile(
            filename="phase8_vendor_policy.pdf",
            file=io.BytesIO(b"%PDF-1.4\n%%EOF"),
        )
        await upload_document(file=fake_file, session=MockSession())  # type: ignore[arg-type]

    assert commit_before_ingestion, "start_ingestion was never called by upload_document()"
    assert commit_before_ingestion[0] is True, (
        "REGRESSION: start_ingestion was called before session.commit(). "
        "The flush/commit race condition is back."
    )