"""Citation excerpting bounds and the substring contract.

``_snap`` walks a window outward to whitespace. It is the one place in the
citation path that indexes by arithmetic, and both of its walks used to step off
the end of the string, so these are regression tests for crashes that really
happened: the end-side one aborted the whole LangGraph run and surfaced to the
user as "Nothing was generated".
"""

from __future__ import annotations

import pytest

from app.rag.citations import _snap, best_excerpt, strip_markers

CHUNK = (
    "Annual leave accrues at 1.75 days per completed calendar month, capped at "
    "25 days per leave year. Unused days may be carried forward."
)


def test_snap_widens_to_whitespace_without_running_off_the_end() -> None:
    # No whitespace anywhere after `start`: the walk must stop at the string end.
    text = "Days:5" + "B" * 40
    snapped = _snap(text, 0, 6)
    assert snapped == text


def test_snap_start_walk_does_not_wrap_to_negative_indices() -> None:
    # One unbroken token, so there is no whitespace to snap to. The window widens
    # to the whole run and stops at index 0; before the fix it wrapped through
    # text[-1], text[-2], ... and raised IndexError. The contract that matters is
    # "no exception, and what comes back is a real substring".
    blob = "x" * 60
    snapped = _snap(blob, 30, 40)
    assert snapped == blob
    assert snapped in blob


def test_snap_widens_to_the_nearest_whitespace() -> None:
    assert _snap("alpha beta gamma", 7, 9) == "beta"
    assert _snap("alpha beta gamma", 6, 10) == "beta"


def test_snap_is_a_noop_when_the_window_already_sits_on_boundaries() -> None:
    assert _snap(CHUNK, 0, 6) == CHUNK[0:6]


def test_snap_at_end_of_text_widens_to_the_final_word() -> None:
    # An empty window at the end still snaps outward, which is the point: it must
    # not index past the final character on the way.
    assert _snap(CHUNK, len(CHUNK), len(CHUNK)) == "forward."
    assert _snap(CHUNK, len(CHUNK), len(CHUNK)) in CHUNK


@pytest.mark.parametrize(
    "query",
    [
        "How many days of annual leave do I get per year?",
        "may I carry unused days forward",
        "25",  # a query with no content terms at all
    ],
)
def test_excerpt_is_always_a_true_substring_of_the_chunk(query: str) -> None:
    # The frontend highlights this exact string, so anything else breaks it.
    assert best_excerpt(CHUNK, query) in CHUNK


def test_excerpt_handles_a_chunk_with_no_whitespace_tail() -> None:
    table = "|City|Nightly cap|" + "|" * 50
    assert best_excerpt(table, "What is the nightly cap?") in table


def test_markers_are_stripped_for_display() -> None:
    assert strip_markers("Leave is 25 days per year [1].") == "Leave is 25 days per year."
    assert strip_markers("See block [2] for caps.") == "See for caps."
    assert strip_markers("") == ""


def test_dangling_connectors_drop_the_aside_sentence() -> None:
    # Observed verbatim from qwen2.5:3b during prompt-injection verification:
    # "... first invoiced project. This information comes from [1]." The second
    # sentence existed only to point at the marker, so it goes with the marker.
    assert (
        strip_markers(
            "Me new Northwind contractors receive a laptop and a 500 USD stipend. "
            "This information comes from [1]."
        )
        == "Me new Northwind contractors receive a laptop and a 500 USD stipend."
    )
    # Trimming words instead of dropping the sentence cannot work: "The cap is
    # described in [2]." gives "The cap is." if only the connector goes, and
    # "The cap." if the copula goes too. Both are fragments.
    assert strip_markers("The cap is described in [2].") == ""
    assert strip_markers("As stated in block [1].") == ""


def test_trailing_connectors_only_never_eat_real_content() -> None:
    """The fix must not truncate a sentence that legitimately ends in a preposition."""
    # "per trip" survives: the sentence does not end in a connector.
    assert (
        strip_markers("Employees may claim up to 1500 USD per trip [1].")
        == "Employees may claim up to 1500 USD per trip."
    )
    # Decimals are split by the sentence regex and must reassemble intact.
    assert (
        strip_markers("Accrual is 1.75 days per month [1].")
        == "Accrual is 1.75 days per month."
    )
    assert strip_markers("The cap is 25 days [1].") == "The cap is 25 days."
