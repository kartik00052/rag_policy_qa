"""RRF fusion correctness.

Cosine and BM25 scores are not comparable, so fusion must use ranks only.
These tests pin that behaviour down with hand-computed expectations.
"""

from __future__ import annotations

from app.services.retrieval import RRF_K, _Hit, reciprocal_rank_fusion


def hit(point_id: str, score: float) -> _Hit:
    return _Hit(point_id=point_id, payload={}, score=score)


def test_rrf_k_is_the_paper_default() -> None:
    assert RRF_K == 60


def test_score_is_sum_of_reciprocal_ranks() -> None:
    fused = {
        s.hit.point_id: s
        for s in reciprocal_rank_fusion(
            [[hit("A", 0.9), hit("B", 0.1)], [hit("B", 99.0), hit("A", 0.0)]]
        )
    }
    # A: dense#1 + sparse#2 ; B: dense#2 + sparse#1
    assert fused["A"].rrf_score == 1 / (RRF_K + 1) + 1 / (RRF_K + 2)
    assert fused["B"].rrf_score == 1 / (RRF_K + 2) + 1 / (RRF_K + 1)


def test_documents_found_by_both_arms_outrank_single_arm_hits() -> None:
    fused = reciprocal_rank_fusion(
        [[hit("A", 0.9), hit("B", 0.8), hit("C", 0.7)], [hit("B", 5.0), hit("A", 4.0)]]
    )
    scores = {s.hit.point_id: s.rrf_score for s in fused}
    assert scores["A"] > scores["C"]
    assert scores["B"] > scores["C"]


def test_raw_scores_are_never_averaged() -> None:
    """A huge BM25 score must not let one arm dominate the other's ranking."""
    fused = {
        s.hit.point_id: s
        for s in reciprocal_rank_fusion([[hit("A", 0.01)], [hit("B", 1e9)]])
    }
    # Each is rank 1 in its own arm, so they score identically.
    assert fused["A"].rrf_score == fused["B"].rrf_score
    assert fused["B"].sparse_score == 1e9
    assert fused["A"].dense_score == 0.01


def test_ranks_and_arm_scores_are_retained() -> None:
    fused = {
        s.hit.point_id: s
        for s in reciprocal_rank_fusion(
            [[hit("A", 0.5), hit("B", 0.4)], [hit("A", 3.0)]]
        )
    }
    a = fused["A"]
    assert (a.dense_rank, a.sparse_rank) == (1, 1)
    assert a.dense_score == 0.5
    assert a.sparse_score == 3.0
    # B was dense-only, so it must not claim a sparse rank.
    assert fused["B"].dense_rank == 2
    assert fused["B"].sparse_rank is None
    assert fused["B"].sparse_score is None


def test_result_ordering_is_score_descending_and_stable() -> None:
    ranking = [hit("A", 0.9), hit("B", 0.8), hit("C", 0.7)]
    order = [s.hit.point_id for s in reciprocal_rank_fusion([ranking])]
    assert order == ["A", "B", "C"]
    # Repeating the call must not reshuffle ties.
    assert order == [s.hit.point_id for s in reciprocal_rank_fusion([ranking])]


def test_empty_arm_contributes_nothing() -> None:
    fused = {s.hit.point_id: s for s in reciprocal_rank_fusion([[hit("A", 0.9)], []])}
    assert fused["A"].rrf_score == 1 / (RRF_K + 1)


def test_both_arms_empty_yields_no_results() -> None:
    assert reciprocal_rank_fusion([[], []]) == []
