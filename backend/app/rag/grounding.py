"""Grounding checks: does the answer agree with the evidence it was given?

Stage 5 already measures a generated answer three ways - it is non-empty, it is
not a numbered block restatement, and it does not copy a block verbatim (see
:mod:`app.rag.restatement`). None of those ask the question that matters most for
a policy tool: *is this claim actually supported by the block it points at?*

Two faults were measured against the sample policy with retrieval and ranking
both already correct, so neither was a Stage 3 or Stage 4 problem:

**1. A governing rule in block 1, ignored.** For ``"Can I book business class for
a five hour flight?"`` the reranker put the governing sentence first with a
cross-encoder logit of ``+3.98`` - the other seven blocks scored between -10.8
and -11.3 - and the model still answered "Yes, business class can be booked for a
five-hour flight". It quoted the *exception* from that same block and dropped
the *mandate* in the sentence before it, because a yes/no question invites a yes.
:func:`find_contradiction` catches that shape.

**2. A correct answer citing the wrong block.** For ``"What is the daily meal
allowance for an international trip?"`` the answer was right ("100 USD") and
pointed at block 2, the per-destination table, whose logit was ``-5.32``. The
citation layer correctly refused to vouch for a block below the evidence
threshold, so the user received a correct fact with no citation at all. The model
had not misunderstood the policy; it had preferred the block that *looked* like
an allowances table over the prose block containing the sentence that answers
the question. :func:`attribute_claims` resolves a claim to the block that
actually carries its text, keeping the model's block number only as a tie-break.

Scope, stated plainly. :func:`find_contradiction` is narrow on purpose: it fires
on a permission-seeking question answered affirmatively whose cited evidence
contains an on-topic requirement the answer never mentions. It is not natural
language inference and will not catch a contradiction that is phrased without a
restrictive modal, or one that turns on comparing two numbers. A narrow check
that never misfires is worth more here than a broad one that starts refusing
correct answers, because every false positive costs a correct answer and a
refusal. :func:`attribute_claims` is the conservative half for the same reason:
it only re-attributes when another block is *strictly* better supported, and it
never attributes to a block below the evidence threshold.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

from app.core.config import get_settings
from app.core.logging import get_logger
from app.schemas.rag import RerankedChunk

logger = get_logger(__name__)


# --------------------------------------------------------------------------
# Citation markers
# --------------------------------------------------------------------------

#: Bracketed markers the model emits to point at a context block.
#:
#: It lives here rather than in :mod:`app.rag.citations` because both modules
#: need it: ``citations`` resolves and strips the markers, and ``grounding`` needs
#: to know which sentence each one belongs to so it can attribute that sentence
#: to a block. Deliberately strict about the contents - only digits and
#: comma-separated digits - so a bracketed word in a quoted document ("see
#: [Appendix]") is not read as a citation. The optional leading word absorbs the
#: phrasing small models prefer ("as described in block [1]"), which lets
#: :func:`~app.rag.citations.strip_markers` remove the whole phrase instead of
#: leaving "in block ," behind.
MARKER = re.compile(
    r"(?:\b(?:block|blocks|source|sources|see)\b[\s:]*)?"
    r"\[(\d{1,3}(?:\s*,\s*\d{1,3})*)\]"
)


def cited_positions(answer: str) -> list[int]:
    """Every block number the answer points at, in order, duplicates kept.

    One marker may carry several numbers ("[1, 3]"); they are flattened here so
    that callers resolving markers do not each re-implement the comma split.
    """
    positions: list[int] = []
    for group in MARKER.findall(answer or ""):
        for part in group.split(","):
            if part.strip():
                positions.append(int(part))
    return positions


# --------------------------------------------------------------------------
# Lexical primitives
# --------------------------------------------------------------------------

_TOKEN = re.compile(r"[a-z0-9]+")

#: Tuned separately from the stopword list in :mod:`app.rag.citations`, and
#: deliberately so. That list decides which window of a chunk to quote, where a
#: common word is noise. This one decides whether two texts are about the same
#: thing, where a modal is exactly the difference between "must be booked" and
#: "is permitted" - so the modals are removed here, but the restriction is
#: detected from the raw sentence by :data:`_RESTRICTIVE` before tokenising.
_STOPWORDS = frozenset(
    """a an the and or but nor of for to in on at by with from into onto over
    under above below about as if then than so not no nor yes is are was were be
    been being am do does did done has have had having will would shall should
    may might must can could it its this that these those there here they them
    their he she his her you your i we our us me my per each any all both more
    most other some such only own same too very also s t just upon within
    without whether""".split()
)


def _stem(token: str) -> str:
    """A deliberately crude suffix strip, applied identically to both sides.

    Both texts in every comparison go through this, so a crude rule only has to
    be *consistent*, not correct: it exists so "flights" and "flight", "booked"
    and "book", "allowances" and "allowance" are one term rather than two, which
    is what stops a paraphrase from looking like a non-sequitur. A real stemmer
    would be a dependency and a source of surprises for no measurable gain here.

    The ``ss`` guard stops "business" becoming "busines", and the trailing-``e``
    strip is what reconciles "allowance" with "allowances".
    """
    for suffix in ("ing", "ed"):
        if len(token) > len(suffix) + 2 and token.endswith(suffix):
            token = token[: -len(suffix)]
            break
    if token.endswith("ies") and len(token) > 4:
        token = token[:-3] + "y"
    elif len(token) > 3 and token.endswith("es"):
        token = token[:-2]
    elif len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        token = token[:-1]
    if len(token) > 3 and token.endswith("e"):
        token = token[:-1]
    return token


def content_tokens(text: str) -> frozenset[str]:
    """Content terms of *text*, stemmed, for "are these about the same thing".

    Numerals are kept even when they are a single character. That is the whole
    point for a policy corpus: "under 6 hours" versus "over 6 hours" is the
    difference between a rule that binds and one that does not, and a length
    filter tuned for words would throw the only token that carries it away.
    """
    tokens: set[str] = set()
    for token in _TOKEN.findall((text or "").lower()):
        if token.isdigit():
            tokens.add(token)
        elif len(token) >= 3 and token not in _STOPWORDS:
            tokens.add(_stem(token))
    return frozenset(tokens)


def coverage(claim: str, source: str) -> float:
    """Fraction of *claim*'s content terms that also occur in *source*.

    Asymmetric on purpose. The question is "does this block carry the content of
    this claim", not "how similar are these two texts", and a claim is short
    while a block is long - a symmetric measure would score a correct
    attribution as low simply because the block says much more than the claim.
    """
    claim_terms = content_tokens(claim)
    if not claim_terms:
        return 0.0
    return len(claim_terms & content_tokens(source)) / len(claim_terms)


#: Sentence boundary: terminal punctuation followed by whitespace, or a line
#: break. Looked at rather than split on the punctuation itself so that "1.75
#: days" stays one sentence - a decimal point is not a full stop.
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")


@dataclass(frozen=True)
class Claim:
    """One sentence of the answer, with the blocks it points at."""

    text: str
    positions: tuple[int, ...]


#: Punctuation and quoting that can be all a marker-fragment leaves behind once
#: the marker itself is removed. ``"`` and ``'`` are included because
#: ``"[1]"`` is often written inside them.
_RESIDUE = ".,;:-\u2013\u2014\"'()[]"


def _prose_left(text: str) -> bool:
    """True when *text* says something beyond its own citation markers.

    The residue trim is what makes this a test about *content* rather than about
    formatting: a fragment of ``"[1]."`` or ``"(see [2])"`` is pure citation
    apparatus, and treating it as a claim would strand its marker instead of
    re-attaching it to the sentence it belongs to.

    Whitespace is stripped before and after the punctuation set, not merely
    folded into it: ``"[1] [2]"`` leaves a single space behind once both markers
    are gone, and a bare space is truthy, so trimming punctuation alone would
    have read a two-marker fragment as prose and stranded both.
    """
    residue = MARKER.sub("", text).strip().strip(_RESIDUE).strip()
    return bool(residue)


def claims_of(answer: str) -> list[Claim]:
    """Split the answer into sentences, each carrying its own citation markers.

    Attribution is per-claim rather than per-answer because a single answer can
    draw on two different blocks. Resolving a marker against the whole answer
    would let the vocabulary of the first sentence vouch for the second.

    A fragment that is *only* a marker - ``"...is 100 USD. [2]"`` splits into a
    claim and a bare ``"[2]"`` - is not a claim at all. Markers are an
    instruction to the model, so a fragment carrying no prose carries no content
    to attribute, and treating it as a claim silently destroyed the citation for
    the real sentence: :func:`attribute_claims` skips claims with no markers, so
    the sentence the marker belonged to was dropped on the floor, and the
    marker's own fragment scored zero coverage against every block and was
    dropped too. That is the whole of the measured "correct answer, no
    citation" fault, and it fired whenever a model placed the marker after the
    full stop rather than against the clause - which is a normal thing for a
    model to do and not something to be defended against downstream. The
    orphan's positions are therefore re-attached to the neighbouring claim
    instead of being stranded, preferring the one before it (the case that
    occurs) and falling back to the one after when the orphan leads.
    """
    claims: list[Claim] = []
    leading: list[int] = []
    for sentence in _SENTENCE_SPLIT.split(answer or ""):
        text = sentence.strip()
        if not text:
            continue
        positions = tuple(cited_positions(text))
        if not _prose_left(text):
            if claims:
                previous = claims[-1]
                claims[-1] = replace(
                    previous, positions=previous.positions + positions
                )
            else:
                leading.extend(positions)
            continue
        if leading:
            positions = tuple(leading) + positions
            leading.clear()
        claims.append(Claim(text=text, positions=positions))
    return claims


# --------------------------------------------------------------------------
# Content-based attribution
# --------------------------------------------------------------------------

#: A block must carry at least this much of a claim before the claim can be
#: attributed to it at all - a majority of the claim's content terms, so that
#: merely sharing a topic is not enough to earn a citation and a highlight.
#:
#: The floor does *not* separate the measured failure's two candidates; the
#: maximum does. The mis-cited meal claim scores 1.00 against the prose block
#: stating the rule and 0.57 against the table the model preferred, so both clear
#: this floor and the one that wins is simply the better-covered block. The floor
#: exists for the case where the model's marker is unusable, so a block has to
#: earn the citation outright rather than by default.
MIN_CLAIM_COVERAGE = 0.5


def attribute_claims(
    answer: str,
    chunks: list[RerankedChunk],
    threshold: float,
    floor: float = MIN_CLAIM_COVERAGE,
) -> list[int]:
    """The 1-based block positions that support *answer*, in order of first use.

    Conservative by construction, and the three rules are each load-bearing:

    * **Only blocks at or above ``threshold`` are ever attributed.** The
      evidence gate's job is to stop the system vouching for a block the
      cross-encoder called non-evidence, and re-attribution must not become a
      way around it.
    * **The model's own block number wins unless another block is strictly
      better supported.** So for the overwhelming majority of answers - where the
      model points at the right block - the outcome is identical to trusting the
      marker, and this function only intervenes when the content disagrees.
    * **When the named block is out of range or below the threshold, the claim
      is re-attributed by content**, and dropped entirely if no block carries it.
      That is the measured meal-allowance fault: the model named a block the
      threshold had already rejected, and today that loses the citation.

    The skip on marker-less claims is deliberate and is the one case this
    function refuses to be clever about. A sentence the model chose not to cite
    is not evidence of anything, and attributing it by content alone would
    invent support the model did not claim. That guard is correct - it is also
    exactly what made a *cited* answer lose its citation, because the marker
    the model did emit landed in its own sentence fragment (see
    :func:`claims_of`), leaving the sentence carrying the content unmarked and
    the marker's fragment empty. The guard is now fed claims whose markers are
    attached to the text they belong to.
    """
    if not chunks:
        return []

    attributed: list[int] = []
    seen: set[int] = set()

    for claim in claims_of(answer):
        if not claim.positions:
            continue
        # Markers are an instruction to the model, not content, so they are
        # removed before measuring coverage - otherwise the literal "1" in
        # "[1]" counts as a term the block has to contain.
        body = MARKER.sub("", claim.text)

        in_range = [p for p in claim.positions if 1 <= p <= len(chunks)]
        named = next(
            (p for p in in_range if chunks[p - 1].score >= threshold), None
        )

        best_position = 0
        best_key: tuple[float, int] | None = None
        for position, chunk in enumerate(chunks, start=1):
            if chunk.score < threshold:
                continue
            score = coverage(body, chunk.chunk.content)
            if score < floor:
                continue
            # Ties go to the block the model named, so this stays a no-op for a
            # correct marker.
            key = (score, 1 if position == named else 0)
            if best_key is None or key > best_key:
                best_key, best_position = key, position

        if best_position == 0:
            if named is not None:
                # Only reachable when the named block is above the threshold but
                # too lexically distant; the claim keeps the model's choice.
                best_position = named
            else:
                logger.warning(
                    "no block at or above the evidence threshold %.3f carries "
                    "the claim %r; leaving it uncited",
                    threshold,
                    claim.text[:120],
                )
                continue

        if best_position != named and named is not None:
            logger.info(
                "re-attributed claim %r from block [%d] to block [%d] on "
                "content coverage",
                claim.text[:80],
                named,
                best_position,
            )

        if best_position not in seen:
            seen.add(best_position)
            attributed.append(best_position)

    return attributed


# --------------------------------------------------------------------------
# Contradiction detection
# --------------------------------------------------------------------------

#: Openings that make a question a request for a ruling rather than a fact.
#: Deliberately excludes "what", "how", "who", "when" and "which": those ask for
#: information the model can only get right by reporting it, so there is no
#: permission to be granted and nothing for a contradiction to contradict.
_POLAR = re.compile(
    r"^\s*(?:can|could|may|might|shall|should|would|will|do|does|did|is|are|"
    r"was|were|has|have|am)\b",
    re.IGNORECASE,
)

#: An answer that grants what was asked. Matched against the opening sentence
#: only: a "no" inside a later caveat ("yes, provided you have approval") is
#: still a yes, and scanning the whole answer would let a passing mention of
#: permission mask a refusal.
_AFFIRMATIVE = re.compile(
    r"""^\s*(?:
          yes | yeah | yep | correct | right | sure | absolutely
        | (?:it|that|this)\s+(?:is|are)
        | you\s+(?:can|may|are|is|will|should|must)
        | (?:can|may)\s+be
        | (?:is|are)\s+(?:allowed|permitted|eligible|acceptable|available|within)
    )\b""",
    re.IGNORECASE | re.VERBOSE,
)

#: A refusal dressed as an answer. Checked against the same opening sentence, so
#: "No, business class is not permitted" is recognised as a refusal rather than
#: read as permission granted - otherwise the check would demand the model drop
#: a clause it had already correctly reported.
_NEGATION = re.compile(
    r"\b(?:not|n't|never|no|cannot|without|unable)\b", re.IGNORECASE
)

#: Wording that makes a sentence a requirement or a prohibition rather than a
#: statement of fact. A policy's governing rule is nearly always phrased this
#: way, which is what makes the check possible without a language model.
_RESTRICTIVE = re.compile(
    r"""\b(?:
          must
        | only
        | never
        | cannot
        | may\s+not
        | shall\s+not
        | not\s+(?:allowed|permitted|eligible|available|entitled|covered)
        | prohibit(?:ed|s|ing)?
        | (?:is|are|was|were|be)\s+required\s+to
        | requir(?:e|es|ed|ing)
        | subj(?:ect|ects)\s+to
    )\b""",
    re.IGNORECASE | re.VERBOSE,
)

#: A restrictive sentence must share at least this many content terms with the
#: question before it counts as being about what was asked. Two is low enough to
#: catch a rule stated in the corpus's own vocabulary ("Economy class must be
#: booked for all flights under 6 hours" shares book/class/hour/flight) and high
#: enough that an unrelated rule elsewhere in the corpus cannot be mistaken for
#: the answer to this question.
MIN_SHARED_TERMS = 2


@dataclass(frozen=True)
class Contradiction:
    """An affirmative answer that ignores a requirement in its own evidence."""

    #: The evidence sentence that governs, quoted for the retry correction.
    clause: str
    #: Terms the clause and the question share, i.e. why it is on topic.
    shared: tuple[str, ...]
    #: Terms the clause requires that the answer never mentions.
    missing: tuple[str, ...]


def find_contradiction(
    query: str, answer: str, chunks: list[RerankedChunk]
) -> Contradiction | None:
    """A requirement in the cited evidence that *answer* contradicts, if any.

    Returns ``None`` for everything else, and the ``None`` is the point: this
    check runs on every polar question, and its cost when it finds nothing is a
    refusal it did not need to issue. The three guards narrow it to the measured
    fault - a permission question answered affirmatively, an on-topic
    requirement in the evidence, and an answer that never mentions it - and each
    is load-bearing. Only blocks at or above the evidence threshold are read, so
    the check cannot object to a rule the rest of the pipeline already declined to
    stand behind.
    """
    if not chunks or not _POLAR.match(query or ""):
        return None

    claims = claims_of(answer)
    if not claims:
        return None
    opening = claims[0].text
    if not _AFFIRMATIVE.match(opening) or _NEGATION.search(opening):
        return None

    threshold = get_settings().evidence_min_score
    question_terms = content_tokens(query)
    answer_terms = content_tokens(answer)

    best: Contradiction | None = None
    for chunk in chunks:
        if chunk.score < threshold:
            continue
        for sentence in _SENTENCE_SPLIT.split(chunk.chunk.content):
            if not _RESTRICTIVE.search(sentence):
                continue
            terms = content_tokens(sentence)
            shared = terms & question_terms
            if len(shared) < MIN_SHARED_TERMS:
                continue
            # What the clause requires that the question did not already supply.
            required = terms - question_terms
            if not required:
                continue
            if required & answer_terms:
                # The answer engages with the rule's own vocabulary - it names
                # the bound, the prohibited class, the threshold - so it is not
                # silently ignoring the rule, however it phrases the verdict. This
                # is what keeps "business class is allowed over 6 hours" from
                # being flagged by the sentence that says economy is mandatory
                # under 6: the second names the bound, the measured failure names
                # neither the bound nor the mandated class. Requiring *total*
                # silence rather than mere absence is the difference between
                # "contradicts its evidence" and "worded it differently".
                continue
            candidate = Contradiction(
                clause=sentence.strip(),
                shared=tuple(sorted(shared)),
                missing=tuple(sorted(required)),
            )
            # Most on-topic requirement wins, so the correction quotes the
            # governing rule rather than some incidental one from the same block.
            if best is None or len(candidate.shared) > len(best.shared):
                best = candidate

    if best is not None:
        logger.info(
            "answer contradicts its evidence: %r requires %s, which the answer "
            "never mentions",
            best.clause[:120],
            ", ".join(best.missing),
        )
    return best
