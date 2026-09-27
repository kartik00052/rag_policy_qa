"""Turn bracketed markers into structured citations (Stage 5).

The model emits ``[1]``, ``[2]`` referring to the numbered context blocks in
:mod:`app.rag.prompts`. This module resolves those markers back to chunks and
attaches to each one a **verbatim excerpt** of the source text.

Why the excerpt is computed here rather than asked of the model: a
cross-encoder score says "this passage is about the question", it does not say
which sentence answers it, and a language model asked to quote a source
paraphrases it. PROJECT.md Section 6 requires ``matched_text`` to be the literal
substring, because the frontend highlights it. So the excerpt is extracted
mechanically from the chunk and :func:`build_citations` asserts the substring
property; a failure there is a bug in this function, not bad luck.

``relevance`` is ``sigmoid(cross_encoder_logit)``, i.e. the model's own
calibrated probability that the passage is relevant. It is in ``[0, 1]`` so it
renders as a score, and negative logits (the model saying "not relevant") become
values below 0.5 rather than being clipped.
"""

from __future__ import annotations

import math
import re

from app.core.config import get_settings
from app.core.logging import get_logger
from app.rag.grounding import MARKER, attribute_claims, cited_positions
from app.schemas.chat import Citation
from app.schemas.rag import RerankedChunk

logger = get_logger(__name__)

#: Bracketed citation markers. Re-exported from :mod:`app.rag.grounding`, which
#: owns the pattern so that the resolution and attribution of a marker cannot
#: drift apart. Deliberately strict about the contents - only digits and
#: comma-separated digits - so a bracketed word in a quoted document ("see
#: [Appendix]") is not read as a citation.
#:
#: The optional leading word absorbs the phrasing small models prefer ("as
#: described in block [1]"). Matching it here means :func:`strip_markers` can
#: remove the whole phrase instead of leaving "in block ," behind.
_MARKER = MARKER

#: Parens/brackets left empty once a marker was removed.
_EMPTY_GROUP = re.compile(r"[\(\[\{]\s*[\)\]\}]")

#: Words too common to localise an excerpt. Not a full stopword list - just the
#: ones that would otherwise match nearly every policy sentence.
_STOPWORDS = frozenset(
    """a an the and or of for to in on at by with is are was were be been being do
    does did how what which when where who whom why can could may might must
    shall should would will i we you they it this that these those my our your
    their its as if then than so not no nor but about from into over under""".split()
)

_TOKEN = re.compile(r"[a-z0-9]+")


def _query_terms(query: str) -> list[str]:
    """Content words from the question, longest first so the rarest anchors."""
    tokens = _TOKEN.findall(query.lower())
    return sorted(
        {t for t in tokens if len(t) >= 3 and t not in _STOPWORDS},
        key=lambda t: (-len(t), t),
    )


def _sigmoid(value: float) -> float:
    # Guarded form: math.exp overflows above ~709.
    if value >= 0:
        return 1.0 / (1.0 + math.exp(-value))
    scaled = math.exp(value)
    return scaled / (1.0 + scaled)


def _snap(text: str, start: int, end: int) -> str:
    """Widen a window to whitespace so it does not cut a word in half.

    Both walks are bounded by their condition on every iteration, not only on
    entry. Guarding the loop body with a one-off ``if`` was a live crash: the
    walk mutates the very index it subscripts, so it stepped past the last
    character and raised ``IndexError: string index out of range`` whenever the
    remainder of the chunk after ``end`` held no whitespace at all (a markdown
    table tail, a URL, a run of separators). That exception escaped
    ``generate_answer``, so the whole graph aborted and the user was told
    "Nothing was generated" instead of being given a cited answer. The start
    walk had the mirror bug: it tested ``start < len(text)`` while decrementing
    towards zero, so it wrapped to negative indices once it reached the start.
    """
    while start > 0 and not text[start - 1].isspace():
        start -= 1
    while end < len(text) and not text[end].isspace():
        end += 1
    return text[start:end]


def best_excerpt(
    content: str, query: str, max_chars: int | None = None
) -> str:
    """The most on-topic verbatim window of ``content``.

    Slides a ``max_chars`` window over every query-term occurrence and keeps the
    window covering the most distinct query terms, which lands on the sentence
    that actually addresses the question rather than the top of the chunk.
    Whitespace is never collapsed and characters are never rewritten, so the
    result is always a true substring of ``content``.
    """
    if max_chars is None:
        max_chars = get_settings().citation_excerpt_max_chars
    text = content.strip()
    if len(text) <= max_chars:
        return text

    terms = _query_terms(query)
    lowered = text.lower()
    positions: list[int] = []
    for term in terms:
        start = 0
        while (found := lowered.find(term, start)) != -1:
            positions.append(found)
            start = found + len(term)
    if not positions:
        return _snap(text, 0, max_chars)

    best_start, best_terms = 0, -1
    for position in positions:
        start = max(0, position - max_chars // 4)
        end = min(len(text), start + max_chars)
        window = lowered[start:end]
        covered = sum(1 for term in terms if term in window)
        # Prefer more query terms; break ties by earlier position for stability.
        if covered > best_terms:
            best_terms, best_start = covered, start

    end = min(len(text), best_start + max_chars)
    return _snap(text, best_start, end)


def has_citation_markers(answer: str) -> bool:
    """True when the model's own text contains at least one ``[n]`` marker.

    Distinct from "did this answer end up with citations". A marker can be
    emitted and then deliberately dropped by :func:`build_citations` because no
    block cleared the evidence threshold while carrying the claim, which is the
    citation layer refusing to vouch rather than the model forgetting to cite.
    Conflating the two once caused a correct, fully-marked answer to be judged
    uncited, retried, and then thrown away as a non-answer, on the grounds that
    the reranker had scored the named block at -5.319. Callers that need to know
    whether the *model* cited anything must ask this, not
    ``bool(build_citations(...))``.

    :func:`build_citations` now re-resolves a marker onto a better-supported
    block, which recovers some of the answers that used to land here, but the
    distinction still has to be made: a model that cites nothing is retryable and
    a claim no block supports is not.
    """
    return bool(_MARKER.search(answer or ""))


def build_citations(
    answer: str, chunks: list[RerankedChunk], query: str
) -> list[Citation]:
    """Resolve the answer's markers into citations, in order of first use.

    A citation id (``c1``, ``c2``, ...) is assigned per answer, so the frontend
    can key highlights on it. Markers outside the supplied range are ignored and
    logged rather than guessed at - an invented reference is worse than a missing
    one, because it would point a user at the wrong policy line.

    Markers no longer decide *which* block is cited, only whether the model
    claimed support at all. The block is resolved by content, in
    :func:`~app.rag.grounding.attribute_claims`, because a marker index is the
    model's guess at provenance and it guesses wrong often enough to matter:
    measured against the sample policy, a correct "100 USD" answer pointed at the
    per-destination allowance *table* (logit -5.32) rather than the prose block
    stating the rule (logit +4.62), so the citation was dropped for naming a block
    the evidence gate had rejected and the user saw a correct fact with nothing
    to check it against. Resolving on content is not a loosening - a citation
    still requires a block that clears the evidence threshold *and* demonstrably
    carries the claim, and the model's own number wins whenever it survives both
    tests.
    """
    settings = get_settings()
    by_position = {position: chunk for position, chunk in enumerate(chunks, start=1)}
    threshold = settings.evidence_min_score

    cited = cited_positions(answer)
    for position in cited:
        if position not in by_position:
            logger.warning(
                "answer cited [%d] but only %d block(s) were supplied",
                position,
                len(chunks),
            )

    ordered = attribute_claims(answer, chunks, threshold)

    # Never cite a block the reranker scored as non-evidence. The top-k handed to
    # the model includes chunks that failed the threshold, and a weak model will
    # happily cite one anyway - observed with qwen2.5:1.5b, which cited an
    # accommodation-limits block (logit -2.48, relevance 0.08) to support a claim
    # about taxi fares. That would highlight the wrong line of the policy, which
    # is worse than showing no citation at all. Reporting the named blocks that
    # did not survive keeps that observable after attribution has had its say.
    dropped = [
        position
        for position in dict.fromkeys(cited)
        if position in by_position and position not in ordered
    ]
    if dropped:
        logger.warning(
            "dropping citation(s) %s: no block at or above the evidence threshold "
            "%.3f carries the claim the marker points at",
            dropped,
            threshold,
        )

    if chunks and not ordered:
        logger.warning(
            "answer cited no blocks despite %d being supplied; returning no citations",
            len(chunks),
        )

    citations: list[Citation] = []
    for order, position in enumerate(ordered, start=1):
        chunk = by_position[position]
        excerpt = best_excerpt(
            chunk.chunk.content, query, settings.citation_excerpt_max_chars
        )
        # The contract the frontend depends on. A violation means the excerpt
        # logic corrupted the text, and must not reach the API.
        assert excerpt in chunk.chunk.content, (
            f"citation excerpt is not a substring of chunk "
            f"{chunk.chunk.point_id}; highlighting would break"
        )
        citations.append(
            Citation(
                id=f"c{order}",
                document_id=chunk.chunk.document_id,
                document_name=chunk.chunk.filename,
                page=chunk.chunk.page_number,
                section=chunk.chunk.section or None,
                matched_text=excerpt,
                relevance=round(_sigmoid(chunk.score), 4),
            )
        )
    return citations


#: Words that only make sense in a phrase like "as stated in [1]" or
#: "this information comes from [1]". Removing the marker leaves the phrase
#: stranded at the end of a sentence, which reads as truncation to a user:
#: "This information comes from." The leading "block"/"source" word is handled
#: separately by ``_EMPTY_GROUP``; this set covers the prepositional tail.
_CONNECTOR_WORDS = frozenset(
    {
        "a", "all", "an", "any", "appears", "appear", "are", "as", "at", "be",
        "been", "by", "comes", "come", "defined", "define", "described",
        "describe", "describes", "document", "documented", "find", "found",
        "from", "given", "give", "in", "into", "is", "listed", "list", "of",
        "on", "outlined", "outlines", "per", "provided", "see", "set", "shown",
        "show", "specified", "specify", "stated", "state", "states", "the",
        "there", "this", "to", "was", "were", "with",
    }
)

#: A connective that cannot end a sentence, so it only ever appears mid-clause
#: governing something that follows it. Listed as alternatives for the regex
#: rather than derived from :data:`_CONNECTOR_WORDS`, which is the opposite case:
#: those words are legal sentence endings in an *uncited* answer and are only
#: stranded when the marker is the last thing in the sentence, so removing them
#: there would damage text that needed no repair.
#:
#: "according to" and "as ... in" are multi-word and are spelled out because a
#: word-boundary alternation would match only "as" or only "to" and leave the
#: other half behind, which is the same dangling-connector bug in miniature.
_DANGLING_LEAD = (
    r"(?:"
    r"according\s+to"
    r"|as\s+(?:per|stated|described|listed|shown|documented|defined|specified)"
    r"(?:\s+in|\s+by)?"
    r"|(?:per|under|with|from|by|in|on|at|of|to|as)"
    r")\b[\s:]*"
)

#: A connective, a marker, and the comma that closed the parenthetical, all of
#: which are removed together. Observed verbatim from the contradiction-retry
#: path: "No, according to [1], economy class must be booked for all flights
#: under 6 hours." Removing the marker alone left "No, according to, economy
#: class must be booked", which reads as a truncation.
#:
#: The trailing comma is what makes this safe and is why it is not simply
#: "strip a dangling connective". The comma proves the marker was parenthetical
#: - inserted into the clause and set off by punctuation, rather than carrying
#: the sentence's own syntax. Without that evidence the connective is load
#: bearing: "The cap is described in [2]." needs its "in" until
#: :func:`_drop_dangling_connectors` decides the whole sentence was an aside, and
#: "Leave is 25 days per year [1]." has no comma to key off at all.
_PARENTHETICAL = re.compile(_DANGLING_LEAD + _MARKER.pattern + r"\s*,")

_SENTENCE_TAIL = re.compile(r"([^.!?]*)([.!?])")


def _drop_dangling_connectors(text: str) -> str:
    """Drop sentences that were only a citation aside.

    A sentence whose last word is a connector existed to point at a marker:
    "This information comes from [1]." Stripping the marker leaves a fragment,
    and trimming words off the end cannot repair it - "The cap is described in
    [2]." becomes "The cap is." if the connector alone is removed, and "The cap."
    if the copula goes too. Both are fragments, so the whole sentence goes
    instead, and the substantive sentence before it survives intact.

    A sentence that does not end in a connector is left alone, so "Leave is 25
    days per year [1]." keeps all of its content, and a decimal such as "1.75"
    is split by the sentence regex but reassembled unchanged because neither "1"
    nor "75" is a connector.
    """

    def fix(match: re.Match[str]) -> str:
        words = match.group(1).split()
        if words and words[-1].strip(",;:" + '"' + "'").lower() in _CONNECTOR_WORDS:
            return ""
        return match.group(0)

    return _SENTENCE_TAIL.sub(fix, text)


def strip_markers(
answer: str) -> str:
    """The answer text with markers removed, for display and storage.

    The markers are an instruction to the model, not content: the numbers live
    on in the citation list, which is what the frontend renders. The leading
    "block"/"source" word is removed with the marker, and any group left empty by
    that removal is collapsed, so ``"... described in block [1]."`` does not become
    ``"... described in block ."``.

    A connector stranded by that removal is then dropped, so
    ``"This information comes from [1]."`` reads as ``"This information."`` rather
    than ``"This information comes from."`` - observed verbatim from qwen2.5:3b
    during prompt-injection verification.

    A marker sitting inside a parenthetical takes its connective and its closing
    comma with it, so ``"No, according to [1], economy class must be booked"``
    becomes ``"No, economy class must be booked"`` - also observed verbatim, from
    the contradiction-retry path, where the model was told to justify its
    correction against a specific block. See :data:`_PARENTHETICAL` for why the
    comma is required and why a bare dangling connective is left alone.
    """
    if not answer:
        return ""
    cleaned = _PARENTHETICAL.sub("", answer)
    cleaned = _MARKER.sub("", cleaned)
    cleaned = _EMPTY_GROUP.sub("", cleaned)
    # Collapse the runs of spaces left behind, but keep paragraph breaks intact.
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"[ \t]+([.,;:!?])", r"\1", cleaned)
    cleaned = _drop_dangling_connectors(cleaned)
    # The connector removal can leave a space before the sentence's punctuation.
    cleaned = re.sub(r"[ \t]+([.,;:!?])", r"\1", cleaned)
    return cleaned.strip()
