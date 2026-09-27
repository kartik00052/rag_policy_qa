"""Pipeline status vocabulary.

``ready`` and ``failed`` are terminal. Reconciliation sweeps up rows stranded in a
non-terminal stage, so treating ``ready`` as in-progress would corrupt good data
on every restart - a regression test for a bug that actually happened.
"""

from __future__ import annotations

import pytest

from app.core.config import (
    FILE_TYPE_BY_EXTENSION,
    IN_PROGRESS_STATUSES,
    PIPELINE_STATUSES,
    SUPPORTED_EXTENSIONS,
    DocumentStatus,
)


def test_pipeline_order_matches_the_sidebar_stages() -> None:
    assert PIPELINE_STATUSES == (
        DocumentStatus.UPLOADING,
        DocumentStatus.PARSING,
        DocumentStatus.CHUNKING,
        DocumentStatus.EMBEDDING,
        DocumentStatus.INDEXING,
        DocumentStatus.READY,
    )


def test_ready_and_failed_are_terminal() -> None:
    assert DocumentStatus.READY not in IN_PROGRESS_STATUSES
    assert DocumentStatus.FAILED not in IN_PROGRESS_STATUSES


def test_in_progress_is_the_pipeline_minus_ready() -> None:
    assert IN_PROGRESS_STATUSES == PIPELINE_STATUSES[:-1]


def test_every_status_is_accounted_for() -> None:
    covered = set(IN_PROGRESS_STATUSES) | {DocumentStatus.READY, DocumentStatus.FAILED}
    assert covered == set(DocumentStatus)


def test_stage_index_is_the_sidebar_progress_position() -> None:
    assert PIPELINE_STATUSES.index(DocumentStatus.PARSING) == 1
    assert PIPELINE_STATUSES.index(DocumentStatus.READY) == len(PIPELINE_STATUSES) - 1


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("policy.pdf", "pdf"),
        ("Policy.DOCX", "docx"),
        ("rates.xlsx", "xlsx"),
        ("roster.csv", "csv"),
    ],
)
def test_supported_extensions_map_to_file_types(filename: str, expected: str) -> None:
    suffix = "." + filename.rsplit(".", 1)[-1].lower()
    assert FILE_TYPE_BY_EXTENSION[suffix] == expected


def test_every_supported_extension_has_a_file_type() -> None:
    assert set(FILE_TYPE_BY_EXTENSION) == set(SUPPORTED_EXTENSIONS)
