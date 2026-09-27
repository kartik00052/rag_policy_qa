"""Unit tests for the evidence gate (Stage 4).

:func:`~app.services.evidence.assess` is pure logic on a list of
:class:`~app.schemas.rag.RerankedChunk` objects, so every test here is
offline - no model, no database, no queue.

The threshold used in every test is 0.0 (the cross-encoder's own decision
boundary, sigmoid 0.5). Tests that need a specific other threshold override
``evidence_min_score`` via monkeypatch.
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest

from app.services.evidence import NO_EVIDENCE_ANSWER, assess
from app.schemas.rag import RerankedChunk
from app.schemas.retrieval import RetrievedChunk


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _chunk(score: float, index: int = 0) -> RerankedChunk:
    retrieved = RetrievedChunk(
        point_id=f"p{index}",
        score=0.05,
        document_id=uuid.UUID(int=1),
        filename="policy.pdf",
        chunk_index=index,
        page_number=1,
        section="2.1 Leave",
        content="Employees accrue 1.75 days of annual leave per month.",
        content_type="text",
    )
    return RerankedChunk(chunk=retrieved, score=score, rrf_score=0.05, rank=index + 1)


def _set_threshold(monkeypatch, threshold: float, min_chunks: int = 1) -> None:
    from app.core import config as cfg
    fake = cfg.Settings.model_construct(
        **{
            **cfg.get_settings().model_dump(),
            "evidence_min_score": threshold,
            "evidence_min_chunks": min_chunks,
        }
    )
    monkeypatch.setattr("app.services.evidence.get_settings", lambda: fake)


# --------------------------------------------------------------------------
# Empty input
# --------------------------------------------------------------------------


def test_empty_list_is_insufficient() -> None:
    decision = assess([])
    assert not decision.sufficient
    assert decision.top_score is None
    assert decision.supporting_chunks == 0
    assert decision.considered == 0


# --------------------------------------------------------------------------
# Single chunk at / above / below threshold
# --------------------------------------------------------------------------


def test_chunk_at_zero_clears_default_threshold() -> None:
    decision = assess([_chunk(0.0)])
    assert decision.sufficient


def test_chunk_above_threshold_clears() -> None:
    decision = assess([_chunk(5.0)])
    assert decision.sufficient
    assert decision.top_score == pytest.approx(5.0)
    assert decision.supporting_chunks == 1


def test_chunk_below_default_threshold_closes_gate() -> None:
    decision = assess([_chunk(-0.001)])
    assert not decision.sufficient
    assert decision.supporting_chunks == 0


# --------------------------------------------------------------------------
# Multiple chunks: required count
# --------------------------------------------------------------------------


def test_two_chunks_above_threshold_opens_gate(monkeypatch) -> None:
    _set_threshold(monkeypatch, threshold=0.0, min_chunks=2)
    decision = assess([_chunk(1.0), _chunk(2.0)])
    assert decision.sufficient
    assert decision.supporting_chunks == 2


def test_one_chunk_above_when_two_required_closes_gate(monkeypatch) -> None:
    _set_threshold(monkeypatch, threshold=0.0, min_chunks=2)
    decision = assess([_chunk(3.0), _chunk(-1.0)])
    assert not decision.sufficient
    assert decision.supporting_chunks == 1
    assert "1 chunk(s)" in decision.reason


def test_top_score_is_always_the_first_chunk(monkeypatch) -> None:
    # The list is assumed pre-sorted descending by score (reranker guarantees this).
    _set_threshold(monkeypatch, threshold=0.0, min_chunks=1)
    chunks = [_chunk(8.0, 0), _chunk(3.0, 1), _chunk(-2.0, 2)]
    decision = assess(chunks)
    assert decision.top_score == pytest.approx(8.0)
    assert decision.considered == 3


# --------------------------------------------------------------------------
# Reason strings
# --------------------------------------------------------------------------


def test_reason_names_threshold_and_score_on_zero_support() -> None:
    decision = assess([_chunk(-3.5)])
    assert "-3.5" in decision.reason or "-3.500" in decision.reason
    assert "threshold" in decision.reason


def test_reason_names_count_when_some_but_not_enough(monkeypatch) -> None:
    _set_threshold(monkeypatch, threshold=0.0, min_chunks=3)
    decision = assess([_chunk(1.0), _chunk(-2.0)])
    assert "1 chunk(s)" in decision.reason
    assert not decision.sufficient


# --------------------------------------------------------------------------
# NO_EVIDENCE_ANSWER constant
# --------------------------------------------------------------------------


def test_no_evidence_answer_is_a_non_empty_string() -> None:
    assert isinstance(NO_EVIDENCE_ANSWER, str)
    assert len(NO_EVIDENCE_ANSWER) > 0


def test_no_evidence_answer_is_distinct_from_failure_messages() -> None:
    from app.rag.graph import GENERATION_FAILED_ANSWER, GENERATION_REJECTED_ANSWER

    assert NO_EVIDENCE_ANSWER != GENERATION_FAILED_ANSWER
    assert NO_EVIDENCE_ANSWER != GENERATION_REJECTED_ANSWER
