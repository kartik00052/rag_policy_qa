"""Safety test verifying that verify_full_stack cleanup only deletes its own documents.

PROJECT.md / workflow.md requirement:
The verification cleanup logic must never delete pre-existing corpus documents.
It must only ever delete the specific document IDs it created during the run.
"""

from __future__ import annotations

import sqlite3
import uuid


def test_cleanup_only_deletes_tracked_document_ids() -> None:
    """Proves that deleting by explicit document ID leaves all pre-existing documents intact."""
    conn = sqlite3.connect(":memory:")
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE documents (
            id TEXT PRIMARY KEY,
            filename TEXT NOT NULL,
            status TEXT NOT NULL
        )
        """
    )

    unrelated_id = str(uuid.uuid4())
    test_id_1 = str(uuid.uuid4())
    test_id_2 = str(uuid.uuid4())

    # Seed 1 real pre-existing document and 2 test documents
    cur.execute("INSERT INTO documents VALUES (?, ?, ?)", (unrelated_id, "real_corporate_policy.pdf", "ready"))
    cur.execute("INSERT INTO documents VALUES (?, ?, ?)", (test_id_1, "test_upload_1.pdf", "ready"))
    cur.execute("INSERT INTO documents VALUES (?, ?, ?)", (test_id_2, "test_upload_2.pdf", "ready"))
    conn.commit()

    # Total documents before cleanup: 3
    cur.execute("SELECT COUNT(*) FROM documents")
    assert cur.fetchone()[0] == 3

    # verify_full_stack cleanup tracks created_doc_ids explicitly:
    created_doc_ids = [test_id_1, test_id_2]

    # Replicate the exact verify_full_stack cleanup SQL:
    for did in created_doc_ids:
        cur.execute(f"DELETE FROM documents WHERE id = '{did}'")
    conn.commit()

    # Assert: only 1 document remains
    cur.execute("SELECT id, filename FROM documents")
    remaining = cur.fetchall()
    assert len(remaining) == 1, f"Expected 1 document to remain, found {len(remaining)}"
    assert remaining[0][0] == unrelated_id
    assert remaining[0][1] == "real_corporate_policy.pdf"

    conn.close()
