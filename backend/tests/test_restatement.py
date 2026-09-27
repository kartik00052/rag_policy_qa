"""Tests for the restatement metric and the answer judge (Stage 5).

The fixtures are the real outputs ``qwen2.5:3b`` produced against the sample
policy, not invented strings. That matters: the ceiling in
:data:`app.rag.restatement.MAX_OVERLAP` is a claim about where this model sits,
and a test built from hand-written prose would only prove the arithmetic works,
not that the threshold is calibrated. If a future model or prompt moves these
numbers, these tests are what should fail first.
"""

from __future__ import annotations

import uuid

import pytest

from app.rag.graph import judge_answer
from app.rag.restatement import (
    MAX_OVERLAP,
    is_restatement,
    longest_common_run,
    ngram_containment,
    words,
    worst_overlap,
)
from app.schemas.rag import RerankedChunk
from app.schemas.retrieval import RetrievedChunk

#: Block 7 of acme_travel_policy.pdf, section 2.2, as retrieved.
SICK_LEAVE_BLOCK = (
    "Full-time employees accrue 10 days of paid sick leave per leave year. "
    "Sick leave may be carried forward into the next leave year up to a maximum "
    "of 5 unused days."
)

#: The model's answer, measured at 8-gram containment 1.00 - a word-for-word copy
#: that still carried a "[1]" marker, so it passed every citation-shaped check.
VERBATIM_ANSWER = (
    "Full-time employees accrue 10 days of paid sick leave per leave year. "
    "Sick leave may be carried forward into the next leave year up to a maximum "
    "of 5 unused days. [1]"
)

#: The retry answer for the same question, measured at 0.00.
SYNTHESIZED_ANSWER = (
    "Full-time employees get 10 days of paid sick leave per year, and they can "
    "carry up to 5 unused days into the next year. [1]"
)

#: A genuine answer that leans on the source wording without copying it. Measured
#: at 0.1961 - the highest value among correct answers observed, and the reason
#: the ceiling is 0.30 rather than something tighter.
NEAR_MISS_ANSWER = (
    "Manager approval is required for all travel. Requests submitted fewer than "
    "5 working days in advance are escalated to the Director for review. [1]"
)


def _chunk(content: str) -> RerankedChunk:
    return RerankedChunk(
        chunk=RetrievedChunk(
            point_id="p1",
            score=0.03,
            document_id=uuid.UUID("73d7e9b1-ebd9-47a3-b5d5-4dd91702c498"),
            filename="acme_travel_policy.pdf",
            chunk_index=0,
            page_number=1,
            section="2.2 Sick Leave",
            content=content,
            content_type="text",
            dense_score=0.5,
            sparse_score=1.0,
            dense_rank=1,
            sparse_rank=1,
        ),
        score=4.2,
        rrf_score=0.03,
        rank=1,
    )


def test_word_tokenisation_drops_punctuation_and_case():
    assert words("Full-time employees: 10 DAYS; sick leave.") == [
        "full",
        "time",
        "employees",
        "10",
        "days",
        "sick",
        "leave",
    ]


def test_verbatim_copy_scores_full_containment():
    assert ngram_containment(SICK_LEAVE_BLOCK, VERBATIM_ANSWER) == 1.0


def test_synthesised_answer_scores_zero_containment():
    assert ngram_containment(SICK_LEAVE_BLOCK, SYNTHESIZED_ANSWER) == 0.0


def test_source_shorter_than_a_shingle_is_not_a_division_error():
    # A one-line table excerpt has no 8-gram to copy. Reporting 0.0 keeps a short
    # block from failing every run by accident.
    assert ngram_containment("Band A, 1500", "anything at all here") == 0.0


def test_ceiling_separates_observed_bands():
    """The two bands the ceiling was placed between must stay separated."""
    assert ngram_containment(SICK_LEAVE_BLOCK, SYNTHESIZED_ANSWER) < MAX_OVERLAP
    assert ngram_containment(SICK_LEAVE_BLOCK, VERBATIM_ANSWER) >= MAX_OVERLAP
    # The tightest correct answer observed still passes with headroom.
    assert ngram_containment(SICK_LEAVE_BLOCK, NEAR_MISS_ANSWER) < MAX_OVERLAP


def test_is_restatement_agrees_with_the_metric():
    assert is_restatement(VERBATIM_ANSWER, [SICK_LEAVE_BLOCK]) is True
    assert is_restatement(SYNTHESIZED_ANSWER, [SICK_LEAVE_BLOCK]) is False


def test_no_blocks_is_not_a_pass_but_not_a_crash():
    ratio, index, run = worst_overlap(SYNTHESIZED_ANSWER, [])
    assert (ratio, index, run) == (0.0, None, 0)


def test_longest_common_run_separates_clause_from_whole_block():
    assert longest_common_run(SICK_LEAVE_BLOCK, VERBATIM_ANSWER) == len(
        words(SICK_LEAVE_BLOCK)
    )
    assert longest_common_run(SICK_LEAVE_BLOCK, SYNTHESIZED_ANSWER) < 12


def test_worst_overlap_picks_the_copied_block():
    other = "Rail travel is preferred for journeys under 400 kilometers."
    ratio, index, _ = worst_overlap(VERBATIM_ANSWER, [other, SICK_LEAVE_BLOCK])
    assert ratio == 1.0
    assert index == 1


def test_judge_accepts_a_synthesised_cited_answer():
    chunks = [_chunk(SICK_LEAVE_BLOCK)]
    ok, reason, correction = judge_answer(SYNTHESIZED_ANSWER, chunks, 1)
    assert ok is True
    assert "overlap" in reason
    assert correction == ""


def test_judge_rejects_a_verbatim_copy_and_names_the_block():
    chunks = [_chunk(SICK_LEAVE_BLOCK)]
    ok, reason, correction = judge_answer(VERBATIM_ANSWER, chunks, 1)
    assert ok is False
    assert "restated block 1" in reason
    # The correction has to be specific enough to act on.
    assert "block 1" in correction
    assert "own words" in correction


def test_judge_rejects_an_answer_with_no_citation_marker():
    chunks = [_chunk(SICK_LEAVE_BLOCK)]
    uncited = SYNTHESIZED_ANSWER.replace(" [1]", "")
    ok, reason, correction = judge_answer(uncited, chunks, 0)
    assert ok is False
    assert reason == "model emitted no citation marker"
    # The correction has to say how to add one, not just that one is missing.
    assert "bracketed number" in correction


def test_judge_accepts_a_marked_answer_whose_citations_were_all_dropped():
    """A marker naming a below-threshold block is not a model failure.

    ``build_citations`` drops markers whose block scored under the evidence
    threshold. That is the citation layer declining to vouch for a weak block, and
    it is not retryable, so judging on the filtered list threw away a correct
    fully-marked answer. Keyed on the model's own text instead.
    """
    chunks = [_chunk(SICK_LEAVE_BLOCK)]
    ok, reason, correction = judge_answer(VERBATIM_ANSWER, chunks, 0)
    # This one is a verbatim copy, so it is still rejected - but for copying.
    assert ok is False
    assert "restated block 1" in reason

    ok, reason, _ = judge_answer(SYNTHESIZED_ANSWER, chunks, 0)
    assert ok is True
    assert "below the evidence threshold" in reason


def test_judge_rejects_a_numbered_block_restatement():
    chunks = [_chunk(SICK_LEAVE_BLOCK)]
    answer = f"(1) {SICK_LEAVE_BLOCK}\n(2) {SICK_LEAVE_BLOCK}"
    ok, reason, _ = judge_answer(answer, chunks, 1)
    assert ok is False
    assert reason == "numbered block restatement"


@pytest.mark.parametrize("answer", ["", "   ", "\n"])
def test_judge_rejects_empty_answers(answer):
    ok, reason, _ = judge_answer(answer, [_chunk(SICK_LEAVE_BLOCK)], 0)
    assert ok is False
    assert reason == "empty answer"
