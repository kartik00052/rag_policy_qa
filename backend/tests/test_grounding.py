"""Grounding: the answer has to agree with the block it points at.

Both faults these cover were measured against the sample policy with retrieval
and ranking already correct - the governing business-class rule was ranked first
at a cross-encoder logit of ``+3.98`` and still got ignored, and a correct meal
allowance answer pointed at a block the evidence gate had rejected at ``-5.32``.
Neither is reachable from a unit test that only exercises the happy path, so the
regressions are pinned here against the exact chunk text and logits that
produced them.

The negative tests matter as much as the positive ones. Both checks can only pay
for themselves by *not* firing on the answers that are already right, so each
guard is exercised with the refusal or the correct answer it must leave alone.
"""

from __future__ import annotations

import uuid

import pytest

from app.rag.citations import build_citations
from app.rag.grounding import (
    MIN_CLAIM_COVERAGE,
    attribute_claims,
    cited_positions,
    claims_of,
    content_tokens,
    coverage,
    find_contradiction,
)
from app.rag.graph import judge_answer
from app.schemas.rag import RerankedChunk
from app.schemas.retrieval import RetrievedChunk

# --- verbatim sample-policy chunks, as measured -----------------------------

AIR = (
    "Economy class must be booked for all flights under 6 hours. Premium economy "
    "is permitted for flights of 6 hours or more, subject to manager approval. "
    "Business class requires written director sign-off."
)
LIMITS = (
    "Employees may claim up to 1500 USD per trip for accommodation. Claims above "
    "1500 USD require written approval from a director before travel is booked.\n\n"
    "Meals are reimbursed at a flat daily allowance of 75 USD for domestic travel "
    "and 100 USD for international travel. Itemised meal receipts are not "
    "required."
)
PER_DIEM_TABLE = (
    "| Destination           |   Daily allowance (USD) | Currency       |\n"
    "|-----------------------|-------------------------|----------------|\n"
    "| New York, USA         |                     100 | USD            |\n"
    "| London, UK            |                      95 | GBP equivalent |\n"
    "| Singapore             |                      85 | SGD equivalent |\n"
    "| Remote / domestic day |                      75 | USD            |"
)


def chunk(content: str, index: int = 0, section: str = "6.2 Reimbursement Limits"):
    return RetrievedChunk(
        point_id=f"point-{index}",
        score=0.03,
        document_id=uuid.UUID(int=1),
        filename="policy.pdf",
        chunk_index=index,
        page_number=1,
        section=section,
        content=content,
        content_type="text",
    )


def blocks(*specs: tuple[str, str, float]) -> list[RerankedChunk]:
    """``blocks(("6.1 Air Travel", AIR, 3.978))`` -> the ranked list chat sees."""
    return [
        RerankedChunk(
            chunk=chunk(content, index=position, section=section),
            score=score,
            rrf_score=0.05,
            rank=position,
        )
        for position, (section, content, score) in enumerate(specs, start=1)
    ]


# --- the business-class fault ------------------------------------------------

BUSINESS_QUERY = "Can I book business class for a five hour flight?"
#: Verbatim from the measured 5-run failure: right block, quoted exception, wrong
#: verdict, no mention of the economy mandate sitting one sentence earlier.
BUSINESS_WRONG = (
    "Yes, business class can be booked for a five-hour flight, provided you have "
    "written director sign-off [1]."
)
BUSINESS_RIGHT = (
    "No. For a five-hour flight you must book economy class, since economy is "
    "mandatory for all flights under 6 hours [1]. Business class is only "
    "available above that with written director sign-off [1]."
)

AIR_BLOCKS = blocks(
    ("6.1 Air Travel and Booking", AIR, 3.978),
    ("9.1 Appendix A: Approval Workflow", "Managers must approve travel.", -10.806),
    ("2.1 Eligibility for Parental Leave", "Leave applies to employees.", -11.265),
)


def test_business_class_answer_contradicting_its_own_block_is_caught() -> None:
    contradiction = find_contradiction(BUSINESS_QUERY, BUSINESS_WRONG, AIR_BLOCKS)
    assert contradiction is not None
    # The clause handed to the retry must be the governing mandate, not the
    # exception the model quoted back at us.
    assert "Economy class must be booked" in contradiction.clause
    # "economy" and the 6-hour bound are what the answer never mentions.
    assert set(contradiction.missing) >= {"economy", "6"}


def test_the_correction_quotes_the_governing_clause_back() -> None:
    ok, reason, correction = judge_answer(
        BUSINESS_WRONG, AIR_BLOCKS, citation_count=1, query=BUSINESS_QUERY
    )
    assert not ok
    assert "contradicts" in reason
    # A correction that does not name the rule gives the retry nothing to obey.
    assert "Economy class must be booked" in correction
    assert "[n]" in correction


def test_the_correction_constrains_the_replys_shape() -> None:
    """Measured: describing the fault makes qwen2.5:3b refuse instead of answer.

    Three wordings that only described what was wrong with the answer each
    returned NOT_IN_DOCUMENTS on 3 of 3 runs, because system-prompt rule 5 is a
    refusal attractor. The surviving form still says the answer was wrong - that
    was not the variable - but it also pins the *shape* of the reply, and the
    model follows the shape. So the correction has to constrain the output, and
    must not assert a bare "No", since a contradicted affirmative is sometimes
    correctly "yes, with approval".
    """
    _, _, correction = judge_answer(
        BUSINESS_WRONG, AIR_BLOCKS, citation_count=1, query=BUSINESS_QUERY
    )
    # The shape requirement is what separates the 3/3 wording from the 0/3 ones.
    assert "Yes or No" in correction
    # The verdict stays the model's to derive from the rule that is quoted back.
    assert "Economy class must be booked" in correction
    assert "so the answer is no" not in correction.lower()
    assert "does not allow" not in correction.lower()


def test_a_correct_negative_answer_is_left_alone() -> None:
    """The check must not demand that a refusal drop a clause it reported."""
    assert find_contradiction(BUSINESS_QUERY, BUSINESS_RIGHT, AIR_BLOCKS) is None
    ok, _, _ = judge_answer(
        BUSINESS_RIGHT, AIR_BLOCKS, citation_count=1, query=BUSINESS_QUERY
    )
    assert ok


def test_an_affirmative_answer_that_states_the_bound_is_left_alone() -> None:
    """Same question shape, permitted case: the bound is acknowledged, so ok."""
    long_flight = (
        "Yes, business class is allowed for flights over 6 hours, with written "
        "director sign-off [1]."
    )
    query = "Can I book business class for a ten hour flight?"
    assert find_contradiction(query, long_flight, AIR_BLOCKS) is None


def test_blocks_below_the_evidence_threshold_cannot_contradict() -> None:
    """The check may not object to a rule the pipeline already declined."""
    demoted = blocks(("6.1 Air Travel and Booking", AIR, -0.5))
    assert find_contradiction(BUSINESS_QUERY, BUSINESS_WRONG, demoted) is None


def test_non_polar_questions_are_out_of_scope() -> None:
    """"What is..." cannot grant permission, so there is nothing to contradict."""
    query = "What is the flight class policy for short trips?"
    assert find_contradiction(query, BUSINESS_WRONG, AIR_BLOCKS) is None


def test_an_unrelated_requirement_elsewhere_is_not_a_contradiction() -> None:
    """Two shared terms is the bar; a rule about a different subject is under it."""
    mixed = blocks(
        ("6.1 Air Travel and Booking", AIR, 3.978),
        (
            "6.2 Reimbursement Limits",
            "Claims above 1500 USD require written approval from a director "
            "before travel is booked.",
            2.0,
        ),
    )
    # The claim is answered outright, so no requirement is left unmentioned.
    answered = (
        "Yes, you may book economy class for this flight, and claims above 1500 "
        "USD require written director approval [2]."
    )
    assert find_contradiction(BUSINESS_QUERY, answered, mixed) is None


# --- the meal-allowance fault ------------------------------------------------

MEAL_QUERY = "What is the daily meal allowance for an international trip?"
#: Correct fact, wrong block: the model chose the allowances *table*.
MEAL_WRONG_BLOCK = (
    "The daily meal allowance is 100 USD for international travel [2]."
)

MEAL_BLOCKS = blocks(
    ("6.2 Reimbursement Limits", LIMITS, 4.620),
    ("6.2 Reimbursement Limits", PER_DIEM_TABLE, -5.319),
)

#: The same claim with the marker removed, which is what attribution measures.
MARKER_REMOVED_MEAL_CLAIM = "The daily meal allowance is 100 USD for international travel."

#: How the model *actually* answered, captured from ``POST /_debug/ask`` in the
#: session that found the fault. It differs from :data:`MEAL_WRONG_BLOCK` in one
#: character that matters: the marker sits after the full stop, in its own
#: sentence fragment. The in-sentence fixture above therefore never exercised the
#: failure, which is how the original fix shipped green.
MEAL_MARKER_AFTER_STOP = (
    "The daily meal allowance for an international trip is 100 USD. [2]"
)


def test_content_beat_the_models_choice_of_block() -> None:
    """The measured fault: [2] is below the threshold, so it cannot be cited."""
    # The prose block carries the claim outright; the table the model preferred
    # shares less than half of it, so the better-covered block wins.
    assert coverage(
        MARKER_REMOVED_MEAL_CLAIM, LIMITS
    ) > coverage(MARKER_REMOVED_MEAL_CLAIM, PER_DIEM_TABLE)
    assert attribute_claims(MEAL_WRONG_BLOCK, MEAL_BLOCKS, threshold=0.0) == [1]
    citations = build_citations(MEAL_WRONG_BLOCK, MEAL_BLOCKS, MEAL_QUERY)
    assert [c.id for c in citations] == ["c1"]
    # And the highlight has to land on the sentence that states the fact.
    assert "100 USD for international travel" in citations[0].matched_text


def test_the_models_block_wins_when_it_is_right() -> None:
    """Attribution must be a no-op for the common case of a correct marker."""
    assert attribute_claims(BUSINESS_RIGHT, AIR_BLOCKS, threshold=0.0) == [1]


def test_a_tie_keeps_the_blocks_the_model_named() -> None:
    tied = blocks(
        ("6.2 Reimbursement Limits", PER_DIEM_TABLE, 4.0),
        ("6.2 Reimbursement Limits", PER_DIEM_TABLE, 3.0),
    )
    # Both blocks carry the claim equally, so the model's [2] is respected.
    assert attribute_claims(
        "The daily meal allowance is 100 USD [2].", tied, threshold=0.0
    ) == [2]


def test_attribution_never_vouches_for_a_block_below_the_threshold() -> None:
    """The invariant the safeguard exists for, across marker and content paths."""
    layouts: list[list[RerankedChunk]] = [
        MEAL_BLOCKS,
        blocks(
            ("6.2 Reimbursement Limits", PER_DIEM_TABLE, 2.0),
            ("6.2 Reimbursement Limits", LIMITS, -5.319),
        ),
        blocks(
            ("6.2 Reimbursement Limits", LIMITS, -1.0),
            ("6.2 Reimbursement Limits", PER_DIEM_TABLE, -9.0),
        ),
    ]
    answers = [
        MEAL_WRONG_BLOCK,
        "The daily meal allowance is 100 USD [1].",
        BUSINESS_RIGHT,
    ]
    for chunk_list, answer in zip(layouts, answers, strict=True):
        for position in attribute_claims(answer, chunk_list, threshold=0.0):
            assert chunk_list[position - 1].score >= 0.0


def test_a_marker_outside_the_supplied_range_is_not_guessed_at() -> None:
    """An invented reference is worse than a missing one."""
    answer = "The daily meal allowance is 100 USD [9]."
    assert attribute_claims(answer, MEAL_BLOCKS, threshold=0.0) == [1]


def test_a_claim_is_re_attributed_between_blocks_that_both_clear_the_gate() -> None:
    """The measured fault, with both blocks above the threshold this time.

    The model named block 1 and block 1 is admissible, so the only thing that
    overrules it is the content: block 2 carries the claim outright and block 1
    covers less than half of it. Stricter-than-before in the useful direction -
    a citation now has to be earned by the block it points at.
    """
    both_above = blocks(
        ("6.2 Reimbursement Limits", PER_DIEM_TABLE, 2.0),
        ("6.2 Reimbursement Limits", LIMITS, 1.0),
    )
    assert attribute_claims(
        "The daily meal allowance is 100 USD for international travel [1].",
        both_above,
        threshold=0.0,
    ) == [2]


def test_nothing_is_attributed_when_every_block_is_below_the_threshold() -> None:
    demoted = blocks(
        ("6.2 Reimbursement Limits", PER_DIEM_TABLE, -0.1),
        ("6.2 Reimbursement Limits", LIMITS, -0.2),
    )
    assert attribute_claims(MEAL_WRONG_BLOCK, demoted, threshold=0.0) == []


# --- an orphaned marker fragment ---------------------------------------------


def test_a_marker_after_the_full_stop_still_attributes_to_its_own_sentence() -> None:
    """The shipped fault, verbatim: a correct answer that cited nothing.

    A model that writes the marker as its own sentence is normal, not adversarial.
    Before the fix the marker became a fragment of its own, the sentence that
    actually carried the claim was left unmarked and skipped, and the citation
    was lost - with the evidence gate reporting it could not find support for a
    claim that was just the string ``"[2]"``.
    """
    claims = claims_of(MEAL_MARKER_AFTER_STOP)
    assert len(claims) == 1
    assert claims[0].positions == (2,)
    # The re-attribution to the prose block, at the exact logits measured live.
    assert attribute_claims(MEAL_MARKER_AFTER_STOP, MEAL_BLOCKS, threshold=0.0) == [1]
    citations = build_citations(MEAL_MARKER_AFTER_STOP, MEAL_BLOCKS, MEAL_QUERY)
    assert [c.id for c in citations] == ["c1"]
    # Never onto the table the model named: still below the evidence threshold.
    assert citations[0].page == 1


def test_an_orphaned_marker_leading_the_answer_joins_the_next_claim() -> None:
    """Mirror image: nothing precedes it, so it attaches forwards."""
    claims = claims_of("[1] The cap is 25 days. Claims must be filed [2].")
    assert [c.positions for c in claims] == [(1,), (2,)]


def test_several_orphan_fragments_all_reach_their_claim() -> None:
    """One prose sentence can be trailed by more than one bare marker."""
    claims = claims_of("The cap is 25 days. [1] [2]")
    assert len(claims) == 1
    assert claims[0].positions == (1, 2)


def test_marker_only_apparatus_is_not_mistaken_for_a_claim() -> None:
    """A fragment with no prose carries no content to attribute."""
    for fragment in ("[1].", "(see [2])", "[3],"):
        assert claims_of(fragment) == [] or not any(
            c.positions for c in claims_of(fragment)
        )


def test_a_claim_the_model_never_cited_is_still_left_alone() -> None:
    """The guard the fix had to route around, not remove.

    Re-attributing a sentence the model chose not to cite would invent support
    it never claimed, so a claim with no marker anywhere must stay uncited even
    when a block covers it completely.
    """
    assert attribute_claims(MARKER_REMOVED_MEAL_CLAIM, MEAL_BLOCKS, threshold=0.0) == []


def test_a_genuine_second_claim_is_not_absorbed_by_an_orphan() -> None:
    """Merging is scoped to marker-only fragments, not to short sentences."""
    claims = claims_of("Yes. The cap is 25 days. [2]")
    assert [c.text for c in claims] == ["Yes.", "The cap is 25 days."]
    assert [c.positions for c in claims] == [(), (2,)]


# --- lexical primitives ------------------------------------------------------


def test_stemming_reconciles_the_word_forms_a_paraphrase_uses() -> None:
    assert content_tokens("flights") == content_tokens("flight")
    assert content_tokens("booked") == content_tokens("book")
    assert content_tokens("allowances") == content_tokens("allowance")
    # A doubled s is not a plural.
    assert content_tokens("business") == frozenset({"business"})


def test_single_digit_bounds_survive_because_they_decide_the_rule() -> None:
    """"under 6 hours" versus "over 6 hours" is the whole difference."""
    assert "6" in content_tokens("flights under 6 hours")
    assert "5" not in content_tokens("a five hour flight")


def test_coverage_is_asymmetric_so_a_correct_match_is_not_penalised() -> None:
    claim = "The daily meal allowance is 100 USD for international travel."
    assert coverage(claim, LIMITS) == pytest.approx(1.0)
    # The table the model preferred carries less of the claim, but still more
    # than the floor - so it is the *maximum* that resolves the attribution, not
    # the floor rejecting the table. Pinning that, because "raise the floor above
    # 0.57" looks like an easy fix and would silently change which blocks are
    # eligible everywhere else.
    table_coverage = coverage(claim, PER_DIEM_TABLE)
    assert table_coverage < 1.0
    assert table_coverage >= MIN_CLAIM_COVERAGE


def test_markers_flatten_and_attach_to_their_own_sentence() -> None:
    assert cited_positions("see [1, 3] and block [2]") == [1, 3, 2]
    claims = claims_of("Leave is 25 days [1]. The cap is 25 days [2].")
    assert [c.positions for c in claims] == [(1,), (2,)]


def test_a_decimal_does_not_split_a_sentence() -> None:
    claims = claims_of("Accrual is 1.75 days per month [1].")
    assert len(claims) == 1
    assert claims[0].positions == (1,)


def test_marker_variations_and_block_capitalization() -> None:
    # BE-01: varied marker formats emitted by LLMs
    assert cited_positions("See Block [2] for details.") == [2]
    assert cited_positions("As described in block 2.") == [2]
    assert cited_positions("Claims under [1-3] apply.") == [1, 2, 3]

    # Capitalized "Block [2]" does not become a phantom claim that drops real claim
    claims = claims_of("The daily meal allowance for an international trip is 100 USD. Block [2]")
    assert len(claims) == 1
    assert claims[0].positions == (2,)
    assert "100 USD" in claims[0].text

