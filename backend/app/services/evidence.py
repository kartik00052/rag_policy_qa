"""Stage 4: does the reranked evidence justify calling the LLM?

PROJECT.md Section 6 requires the assistant to say it could not find something
in the documents rather than answer from general knowledge. A language model will
happily answer a travel policy question about gym membership subsidies, so the
refusal has to happen *before* the model sees the question.

This module is that decision, and nothing else. It reads reranked chunks, applies
the threshold calibrated in ``scripts/calibrate_evidence.py``, and returns a
verdict carrying the numbers behind it so a surprising answer can be diagnosed
without re-running the cross-encoder.

The gate is deliberately allowed to be wrong in one direction: it would rather
let a well-supported question through to the model (which is instructed to stay
inside the context) than block a real answer. See the failure modes documented on
``Settings.evidence_min_score``.
"""

from __future__ import annotations

from app.core.config import get_settings
from app.core.logging import get_logger
from app.schemas.rag import EvidenceDecision, RerankedChunk

logger = get_logger(__name__)

#: PROJECT.md Section 6 wording for the refusal. Returned verbatim when the gate
#: closes, so the user is never left wondering whether the model was consulted.
NO_EVIDENCE_ANSWER = (
    "I couldn't find this in the documents. "
    "Try rephrasing your question, or check that a policy covering this topic "
    "has been uploaded."
)


def assess(chunks: list[RerankedChunk]) -> EvidenceDecision:
    """Decide whether ``chunks`` carry enough signal to answer from.

    Requires ``evidence_min_chunks`` chunks at or above ``evidence_min_score``.
    Because the list is ordered by descending cross-encoder score, counting over
    it is equivalent to counting over the full candidate set.
    """
    settings = get_settings()
    threshold = settings.evidence_min_score
    required = settings.evidence_min_chunks

    if not chunks:
        return EvidenceDecision(
            sufficient=False,
            reason="retrieval returned no candidates",
            threshold=threshold,
            top_score=None,
            supporting_chunks=0,
            considered=0,
        )

    top_score = chunks[0].score
    supporting = sum(1 for chunk in chunks if chunk.score >= threshold)

    if supporting < required:
        reason = (
            f"best cross-encoder logit {top_score:.3f} < threshold {threshold:.3f}"
            if supporting == 0
            else f"only {supporting} chunk(s) cleared {threshold:.3f}, need {required}"
        )
        logger.info("evidence gate closed: %s", reason)
        return EvidenceDecision(
            sufficient=False,
            reason=reason,
            threshold=threshold,
            top_score=top_score,
            supporting_chunks=supporting,
            considered=len(chunks),
        )

    logger.info(
        "evidence gate open: %d/%d chunk(s) at or above %.3f (best %.3f)",
        supporting,
        len(chunks),
        threshold,
        top_score,
    )
    return EvidenceDecision(
        sufficient=True,
        reason=f"{supporting} chunk(s) at or above {threshold:.3f}",
        threshold=threshold,
        top_score=top_score,
        supporting_chunks=supporting,
        considered=len(chunks),
    )
