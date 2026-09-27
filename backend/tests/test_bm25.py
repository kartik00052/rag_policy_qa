"""BM25 term weighting and sparse-vector construction."""

from __future__ import annotations

from app.services import bm25
from app.services.corpus_stats import CorpusStats


def test_tokenize_lowercases_and_keeps_outline_numbers() -> None:
    assert bm25.tokenize("Section 2.1 Eligibility") == ["section", "2.1", "eligibility"]
    assert bm25.tokenize("Six Months") == ["six", "months"]


def test_tokenize_drops_punctuation_only_tokens() -> None:
    assert bm25.tokenize("|---|---|") == []


def test_term_index_is_stable_and_in_range() -> None:
    first = bm25.term_index("parental")
    assert first == bm25.term_index("parental")
    assert 0 <= first <= bm25._MAX_INDEX
    assert bm25.term_index("parental") != bm25.term_index("sick")


def test_doc_weights_are_length_normalised() -> None:
    """A term filling more of the chunk must weigh more than a rare-in-length one."""
    short = bm25.bm25_doc_weights(bm25.tokenize("leave leave leave policy"), avgdl=4.0)
    long = bm25.bm25_doc_weights(
        bm25.tokenize("leave " + "filler " * 50), avgdl=4.0
    )
    assert short["leave"] > long["leave"]


def test_sparse_vector_indices_and_values_align() -> None:
    vector = bm25.chunk_sparse_vector("paid parental leave", avgdl=3.0)
    assert len(vector.indices) == len(vector.values)
    assert all(index >= 0 for index in vector.indices)


def test_query_vector_uses_corpus_idf() -> None:
    """A term in every document must contribute less than a rare one."""
    stats = CorpusStats(total_chunks=2, doc_freq={"common": 2, "rare": 1})
    vector = bm25.query_sparse_vector("common rare", stats)
    weights = dict(zip(vector.indices, vector.values, strict=True))
    assert weights[bm25.term_index("rare")] > weights[bm25.term_index("common")]


def test_query_with_no_known_terms_still_builds_a_vector() -> None:
    """Unknown terms keep max IDF (standard BM25) but match no chunk, so they
    cannot inflate any document's score - the sparse dot product ignores them."""
    stats = CorpusStats(total_chunks=1, doc_freq={"leave": 1})
    vector = bm25.query_sparse_vector("zzz qqq", stats)
    assert set(vector.indices) == {
        bm25.term_index("zzz"),
        bm25.term_index("qqq"),
    }
    indexed = bm25.chunk_sparse_vector("leave", avgdl=1.0)
    assert not set(vector.indices) & set(indexed.indices)


def test_empty_query_yields_an_empty_vector() -> None:
    stats = CorpusStats(total_chunks=1, doc_freq={"leave": 1})
    assert list(bm25.query_sparse_vector("   ", stats).indices) == []


def test_repeated_query_term_is_not_double_counted() -> None:
    stats = CorpusStats(total_chunks=2, doc_freq={"leave": 2})
    once = bm25.query_sparse_vector("leave", stats)
    thrice = bm25.query_sparse_vector("leave leave leave", stats)
    assert list(once.indices) == list(thrice.indices)
    assert list(once.values) == list(thrice.values)
