"""Detect and quantify verbatim restatement of a context block (Stage 5).

A local instruction-tuned model answering from numbered context blocks will
sometimes take the path of least resistance and reproduce a block instead of
answering from it. Written rules in the prompt reduce this but do not remove it:
measured against the sample policy with ``qwen2.5:3b``, a block was reproduced
word for word (8-gram containment 1.00) even when the system prompt demonstrated
the correct synthesised answer for that exact block and question alongside the
wrong one. So the request path measures the output rather than trusting it, and
retries when the measurement says the model copied.

The metric is word-level n-gram *containment*:

    containment(block, answer) =
        |{ n-grams of block that also occur in answer }| / |{ n-grams of block }|

"what fraction of this source block is reproduced word for word, in order".
Containment rather than a symmetric similarity, because the block is usually far
longer than the answer: a correct answer has near-zero overlap with the blocks it
did not use, and Jaccard would score that harmless near-miss as copying.

Why n = 8: eight consecutive words is roughly 45 characters of exact match.
Independently written sentences about the same fact do not share a run that long
by chance - the longest accidental run between unrelated English prose is on the
order of five or six words, almost always function-word stacking like "of the",
which :func:`longest_common_run` reports separately as a diagnostic. Clause-level
copying, which is the failure, reproduces 8-grams nearly one for one, so
containment climbs toward 1.0.

:data:`MAX_OVERLAP` is the calibrated ceiling, and the calibration is the reason
this module is not just a test helper. Measured on the sample policy:

    genuine synthesised answers   0.00 - 0.245
    confirmed restatements        0.41 - 1.00

The two bands do not touch, so the ceiling sits in the gap at 0.30. That is the
number the request path enforces and the verification scripts assert, so the
test and the runtime agree on what "copied" means instead of drifting apart.
"""

from __future__ import annotations

import re

#: Words per shingle. See the module docstring for the justification.
DEFAULT_NGRAM = 8

#: Calibrated ceiling on 8-gram containment between an answer and any single
#: context block. Sits in the measured gap between synthesised answers (<= 0.245)
#: and restatements (>= 0.41); see the module docstring.
MAX_OVERLAP = 0.30

#: A run this long is clause-level copying, reported as a diagnostic.
RUN_DIAGNOSTIC_WORDS = 12

_TOKEN = re.compile(r"[a-z0-9]+")


def words(text: str) -> list[str]:
    """Lowercased alphanumeric tokens, so punctuation cannot mask a real copy."""
    return _TOKEN.findall((text or "").lower())


def ngram_containment(source: str, target: str, n: int = DEFAULT_NGRAM) -> float:
    """Fraction of *source*'s n-grams that appear, in order, inside *target*.

    Returns 0.0 when either side is shorter than *n*: a short source has no
    n-grams to be copied, and treating that as "nothing to compare" rather than
    dividing by zero keeps a one-line table excerpt from failing a run.
    """
    source_words = words(source)
    target_words = words(target)
    if len(source_words) < n or len(target_words) < n:
        return 0.0
    target_grams = {
        tuple(target_words[i : i + n]) for i in range(len(target_words) - n + 1)
    }
    total = len(source_words) - n + 1
    hits = sum(
        1 for i in range(total) if tuple(source_words[i : i + n]) in target_grams
    )
    return hits / total


def longest_common_run(source: str, target: str) -> int:
    """Longest contiguous word-for-word run shared by *source* and *target*.

    A diagnostic rather than a gate: it separates "reproduced a clause" from
    "reproduced the whole block", which containment alone cannot show.
    """
    source_words = words(source)
    target_words = words(target)
    best = 0
    for i in range(len(source_words)):
        for j in range(len(target_words)):
            run = 0
            while (
                i + run < len(source_words)
                and j + run < len(target_words)
                and source_words[i + run] == target_words[j + run]
            ):
                run += 1
            if run > best:
                best = run
                if best == len(source_words) or best == len(target_words):
                    return best
    return best


def worst_overlap(
    answer: str, blocks: list[str], n: int = DEFAULT_NGRAM
) -> tuple[float, int | None, int]:
    """The block most copied into *answer*.

    Returns ``(containment, block_index, longest_run)``. ``block_index`` is
    ``None`` when there are no blocks, which callers treat as "nothing to compare
    against" rather than as a pass.
    """
    if not blocks:
        return 0.0, None, 0
    best_ratio, best_index, best_run = 0.0, 0, 0
    for index, block in enumerate(blocks):
        ratio = ngram_containment(block, answer, n)
        if ratio > best_ratio:
            best_ratio, best_index = ratio, index
        best_run = max(best_run, longest_common_run(block, answer))
    return best_ratio, best_index, best_run


def is_restatement(answer: str, blocks: list[str], ceiling: float = MAX_OVERLAP) -> bool:
    """True when *answer* reproduces too much of any single block."""
    ratio, _, _ = worst_overlap(answer, blocks)
    return ratio >= ceiling
