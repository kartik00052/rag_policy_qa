"""LangGraph orchestration for retrieval-grounded answers (Stage 5).

PROJECT.md Section 4 pins the node sequence:

    retrieve_hybrid -> rerank -> check_evidence -> generate_answer -> END
                       with check_evidence -> END as a short-circuit

The short-circuit is the important part. When the gate closes, the LLM is never
invoked, so a question the corpus cannot answer cannot be answered from the
model's own knowledge. That is a structural guarantee rather than a prompt
request, which is why it lives in the edge wiring instead of in the prompt.

Two design points worth stating plainly:

* **Node errors are recorded, not raised.** LangGraph aborts the whole run on an
  uncaught exception, and Stage 6 must still emit a well-formed final event with
  ``has_sufficient_evidence: false`` and a usable message. So provider failures
  are caught at the node boundary, logged with a traceback, and recorded on
  ``RAGState.error`` before the refusal is returned. This is not error
  *swallowing*: the user-visible text names the failure, the operator gets the
  traceback, and the raw message is on the state for the debug endpoint.

* **Tokens leave through a queue, not a return value.** A LangGraph node returns
  a state update; it cannot yield. So :func:`generate_answer_node` pushes each
  piece into the ``token_sink`` queue carried on the state, and
  :func:`stream_answer` drains that queue while the graph is still running. The
  node still does the generation - the queue is only the delivery channel.

* **First attempt generated silently, validated, then replayed.** The first
  generation is collected without touching the token sink, the validator runs
  against the full text, and only the *accepted* answer is replayed to the
  client. Each piece is re-emitted at the original streaming cadence so the
  client still receives incremental tokens. A rejected attempt is retried the
  same way it always was - silently - and the accepted retry is replayed
  instead. The client never receives a token from an answer that failed
  validation, which is the correct behaviour for a policy tool.

  **Measured retry rate (completed 5-run verify_generation suite, 2026-09-27):**
  ``run=1`` never occurred across all 9 answered questions - every single answer
  needed at least one retry, and one question needed 17. The previous docstring
  said "16 of 18 needed no retry"; that figure was measured before the
  ``FEW_SHOT_EXAMPLE`` and ``RETRY_CORRECTION`` mitigations were introduced and
  no longer reflects this build's behaviour. Clients must still treat
  ``done.answer`` as authoritative over accumulated tokens: the SSE contract
  already carries the full text there, and a mid-stream disconnect can leave the
  replay incomplete.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator
from typing import Any

from langgraph.graph import END, START, StateGraph

from app.core.config import get_settings
from app.core.logging import get_logger
from app.rag.citations import (
    build_citations,
    has_citation_markers,
    strip_markers,
)
from app.rag.grounding import find_contradiction
from app.rag.prompts import (
    NOT_IN_DOCUMENTS,
    build_context,
    build_messages,
    build_retry_messages,
)
from app.rag.restatement import MAX_OVERLAP, worst_overlap
from app.rag.state import RAGState
from app.services.evidence import NO_EVIDENCE_ANSWER, assess
from app.services.llm import ProviderStats, get_llm
from app.services.reranker import rerank
from app.services.retrieval import (
    hits_to_retrieved_chunks,
    retrieve_arms,
    to_retrieved_chunks,
)

logger = get_logger(__name__)

#: Written into the answer when a node fails. It is deliberately explicit: a
#: user must be able to tell "this is not in your documents" apart from "the
#: assistant is broken", because the two lead to different next actions.
GENERATION_FAILED_ANSWER = (
    "I couldn't reach the language model, so I can't answer this right now. "
    "Your question was not answered from the documents."
)

#: Written when a generation was rejected twice: the model kept restating a
#: context block instead of answering.
#:
#: Deliberately *not* the evidence gate's
#: :data:`~app.services.evidence.NO_EVIDENCE_ANSWER`. The gate did open - it
#: judged the corpus plausible - so telling the user "couldn't find this in the
#: documents" would be false, and a user who believes their policy is missing
#: something has been actively misled. It also makes the outcome observable: the
#: two refususes are different strings, so verification can tell a Stage 4
#: recall miss from a Stage 5 generation failure instead of scoring both as the
#: same thing.
GENERATION_REJECTED_ANSWER = (
    "The assistant retrieved relevant policy text but could not turn it into a "
    "reliable answer, so nothing has been answered from the documents. Try "
    "rephrasing the question, or check that a policy covering this topic is "
    "uploaded."
)


async def retrieve_hybrid_node(state: RAGState) -> dict[str, Any]:
    """Stage 3 hybrid retrieval, exposed per arm so the state is inspectable."""
    settings = get_settings()
    document_ids = state.get("document_ids")
    dense_hits, sparse_hits, fused = await retrieve_arms(
        state["query"],
        candidate_limit=settings.rerank_candidate_limit,
        document_ids=document_ids,
    )
    candidates = to_retrieved_chunks(fused, settings.rerank_candidate_limit)
    logger.info(
        "retrieve_hybrid: dense=%d sparse=%d fused=%d",
        len(dense_hits),
        len(sparse_hits),
        len(fused),
    )
    return {
        "dense_results": hits_to_retrieved_chunks(dense_hits),
        "sparse_results": hits_to_retrieved_chunks(sparse_hits),
        "candidates": candidates,
    }


async def rerank_node(state: RAGState) -> dict[str, Any]:
    """Stage 4 cross-encoder rerank down to the context budget."""
    chunks: list = state.get("candidates") or []
    ranked = await rerank(state["query"], chunks)
    # Context is built here rather than in the generation node so the assembled
    # prompt is visible in state even on the paths where no LLM call happens.
    return {"reranked_chunks": ranked, "context": build_context(ranked)}


async def check_evidence_node(state: RAGState) -> dict[str, Any]:
    """Stage 4 gate. On failure it writes the refusal so the graph can end."""
    decision = assess(state.get("reranked_chunks") or [])
    update: dict[str, Any] = {
        "has_sufficient_evidence": decision.sufficient,
        "evidence": decision,
    }
    if not decision.sufficient:
        # Short-circuit target: the answer is decided here, without the model.
        update["answer"] = NO_EVIDENCE_ANSWER
        update["citations"] = []
    return update


#: A bare ``(1) (2) <text>`` block restatement: numbered items whose text is the
#: source block. Seen from qwen2.5:3b before the retry path existed. Kept as a
#: separate signal from the overlap measurement because it names a different fault
#: (a numbered list of blocks rather than one block copied inline), and the retry
#: correction reads better when it can say which one happened.
_NUMBERED_RESTATEMENT = re.compile(r"^\s*\(\d+\)")


def judge_answer(
    raw: str, chunks: list, citation_count: int, query: str = ""
) -> tuple[bool, str, str]:
    """Decide whether a generated answer is a real answer.

    Returns ``(ok, reason, correction)``. ``reason`` names the fault and is logged;
    ``correction`` is the retry instruction, which differs by fault because a
    "you cited nothing" answer needs a different nudge than a verbatim copy.

    Four faults, all observed with this model on the sample policy:

    * the model emitted no citation marker at all - it referenced its source in
      prose ("this can be found in Table 6.2a of Appendix A") and left the user
      with claims they could not check;
    * a numbered block restatement;
    * verbatim overlap at or above :data:`~app.rag.restatement.MAX_OVERLAP` with
      any single block;
    * the answer affirms a permission that its own cited evidence restricts -
      the check added for the business-class fault, see below.

    The contradiction check is deliberately a measurement rather than a prompt
    instruction. The business-class answer was wrong while quoting the very block
    that forbids it, with that block ranked first at a cross-encoder logit of
    +3.98, so retrieval and ranking were already right and nothing in the prompt
    was going to change the outcome. :func:`~app.rag.grounding.find_contradiction`
    inspects the answer against the chunks instead, and the correction quotes the
    governing clause back so the retry has something concrete to obey. It is
    checked before the overlap test because a contradiction is the more dangerous
    of the two: an answer that is both copied and wrong fails the factuality
    requirement anyway, and telling the model it copied something is no help.

    Deliberately keyed on markers in the model's own text, not on
    ``citation_count``. A marker can be emitted and then dropped by
    :func:`~app.rag.citations.build_citations` because no block clearing the
    evidence threshold carries the claim; that is the citation layer declining to
    vouch, not the model failing to cite, and retrying cannot fix it because the
    same block will be dropped again. Judging on the filtered list turned a
    correct, fully-marked answer into a hard refusal.
    """
    if not raw.strip():
        return False, "empty answer", "Answer the question, even if briefly."
    if _NUMBERED_RESTATEMENT.search(raw):
        # Checked before the marker test: a numbered block restatement never
        # carries a marker either, and naming that shape gives the model a far
        # more useful correction than "you cited nothing".
        return (
            False,
            "numbered block restatement",
            "Do not output a numbered list of the context blocks. Write plain "
            "prose that answers the question, citing the blocks you use as [n].",
        )
    if not has_citation_markers(raw):
        return (
            False,
            "model emitted no citation marker",
            "Your answer cited no block. Add the bracketed number of the block each "
            "claim came from, immediately after the claim.",
        )
    contradiction = find_contradiction(query, raw, chunks)
    if contradiction is not None:
        # Wording measured against qwen2.5:3b, not designed. Three wordings that
        # only described the fault - including two that opened by saying the
        # answer was wrong - each produced NOT_IN_DOCUMENTS on 3 of 3 runs. Rule 5
        # of the system prompt is a refusal attractor, and a correction that
        # reports a failure gives the model a reason to refuse. What works is
        # constraining the *shape* of the reply instead: state the rule, then
        # require the verdict to follow from it. Forcing a bare "No" also
        # measured 3 of 3, but it is not available here - a contradicted
        # affirmative is sometimes correctly "yes, with approval" - so the lead
        # is left to the model to derive.
        return (
            False,
            f"answer contradicts its cited evidence: {contradiction.clause!r}",
            f'Your first answer was wrong. The block you cited states: '
            f'"{contradiction.clause}" Answer again, and your answer must begin '
            f"with Yes or No followed by the reason, matching what that rule "
            f"actually requires. Cite the block as [n].",
        )
    blocks = [chunk.chunk.content for chunk in chunks]
    ratio, index, run = worst_overlap(raw, blocks)
    if ratio >= MAX_OVERLAP:
        return (
            False,
            f"restated block {index + 1} (overlap {ratio:.2f}, longest run {run} words)",
            f"That answer copied context block {index + 1} word for word, so it is "
            "not an answer. Answer the question again, keeping every fact but "
            "rewriting the sentences in your own words. Cite the block as [n].",
        )
    if citation_count == 0:
        # Marked, but every marker named a block the evidence threshold rejected.
        # Not the model's fault and not retryable, so it is recorded and the
        # answer stands - build_citations has already logged which blocks were
        # dropped and why.
        return True, "markers present but all citations below the evidence threshold", ""
    return True, f"ok (overlap {ratio:.2f})", ""


async def _collect(
    messages: list[dict[str, str]],
) -> tuple[str, list[str], ProviderStats | None]:
    """Run one generation, returning the joined text, the raw piece list, and stats.

    The piece list is needed by :func:`_replay` so validated answers can be
    sent to the client at token granularity without re-running the LLM. The
    stats are Ollama's own timing breakdown, carried through so the retry count
    and the cost of each attempt can be reported as measurements rather than
    inferred from wall-clock. ``None`` means the provider reported no timings.
    Always silent: callers decide whether and when to replay the pieces.
    """
    pieces: list[str] = []
    stats: list[ProviderStats] = []
    async for piece in get_llm().stream(messages, on_stats=stats.append):
        pieces.append(piece)
    return "".join(pieces), pieces, (stats[0] if stats else None)


async def _replay(pieces: list[str], sink: asyncio.Queue) -> None:
    """Replay a pre-collected generation into *sink* piece by piece.

    The inter-piece sleep matches the original streaming cadence rather than
    dumping all tokens at once, so the client experience is indistinguishable
    from a live stream of the same answer. The delay is not precision-critical:
    a fixed 80ms is inside the measured inter-token gap of 80–110ms and short
    enough that a replay of any reasonable answer adds under 5 seconds of
    additional wait beyond the generation time.
    """
    for piece in pieces:
        await sink.put(piece)
        await asyncio.sleep(0.08)


async def generate_answer_node(state: RAGState) -> dict[str, Any]:
    """Stage 5 generation. Only reached when the gate opened."""
    settings = get_settings()
    chunks = state.get("reranked_chunks") or []
    sink = state.get("token_sink")
    messages = build_messages(state["query"], chunks)
    query = state["query"]

    # Generation cost accounting. `attempts` is the number of provider calls
    # actually made, and `attempt_stats` carries Ollama's own timing for each, so
    # the retry rate and the per-attempt latency are both measurements on the
    # state rather than something inferred from a log line. Reported by
    # /api/v1/_debug/ask; the documented ChatFinal contract is untouched.
    attempts = 0
    attempt_stats: list[ProviderStats] = []

    def _account(stats: ProviderStats | None) -> None:
        nonlocal attempts
        attempts += 1
        if stats is not None:
            attempt_stats.append(stats)

    def _telemetry(**extra: Any) -> dict[str, Any]:
        """The state keys every exit path below returns, plus per-path detail."""
        payload: dict[str, Any] = {
            "attempts": attempts,
            "generation_stats": attempt_stats,
            "model": settings.ollama_model,
        }
        payload.update(extra)
        return payload

    def _log_summary(outcome: str) -> None:
        """One line per question: how many calls, what each cost, and the result.

        The retry rate and the latency of a retry are both invisible from the
        client - the SSE contract reports only the final answer - so they are
        logged here where a run of the verification suite can be measured
        against them.
        """
        for index, stats in enumerate(attempt_stats, start=1):
            logger.info(
                "generation attempt %d/%d model=%s query=%r outcome=%s "
                "prompt_tokens=%d prefill=%.2fs (%.0f tok/s) "
                "eval_tokens=%d decode=%.2fs (%.1f tok/s) load=%.2fs total=%.2fs "
                "done_reason=%s",
                index,
                attempts,
                settings.ollama_model,
                query[:80],
                outcome if index == attempts else "rejected",
                stats.prompt_eval_count,
                stats.seconds("prompt_eval_duration"),
                stats.prompt_tokens_per_second,
                stats.eval_count,
                stats.seconds("eval_duration"),
                stats.tokens_per_second,
                stats.seconds("load_duration"),
                stats.seconds("total_duration"),
                stats.done_reason,
            )
        if not attempt_stats:
            logger.info(
                "generation made %d call(s) to %s for %r with no provider timings; "
                "outcome=%s",
                attempts,
                settings.ollama_model,
                query[:80],
                outcome,
            )

    # First attempt is always silent. The text is validated below; only the
    # accepted answer is replayed to the sink. This ensures the client never
    # receives token events from an answer that fails validation.
    try:
        raw, first_pieces, first_stats = await _collect(messages)
    except Exception as exc:  # noqa: BLE001 - surfaced below, not swallowed
        _account(None)
        logger.exception("generation failed for model %s", settings.ollama_model)
        return _telemetry(
            answer=GENERATION_FAILED_ANSWER,
            citations=[],
            has_sufficient_evidence=False,
            error=str(exc),
            outcome="provider-error",
        )
    _account(first_stats)

    if not raw.strip():
        logger.warning("provider returned an empty answer for %r", query)
        _log_summary("empty-answer")
        return _telemetry(
            answer=GENERATION_FAILED_ANSWER,
            citations=[],
            has_sufficient_evidence=False,
            error="provider returned an empty answer",
            outcome="empty-answer",
        )

    # The model can decline even when the gate opened - it read the blocks and
    # found no answer, which is the cross-encoder false-positive case (a passage
    # about the right topic that does not answer the actual question). That is
    # a refusal, not a failure, so it is reported the same way as the gate's
    # refusal is: no citations, and not enough evidence.
    if NOT_IN_DOCUMENTS in raw:
        logger.info("model declined after the gate opened; treating as no evidence")
        _log_summary("declined")
        return _telemetry(
            raw_answer=raw,
            answer=NO_EVIDENCE_ANSWER,
            citations=[],
            has_sufficient_evidence=False,
            outcome="declined",
        )

    citations = build_citations(raw, chunks, query)
    ok, reason, correction = judge_answer(raw, chunks, len(citations), query)
    if not ok:
        logger.warning(
            "generated answer rejected (%s) for %r; retrying once with a correction",
            reason,
            query,
        )
        # Kept so the retry's own outcome can be judged against what the first
        # attempt got wrong, which the first attempt's text is the only record of.
        first_attempt = raw
        accepted_pieces: list[str] = []
        try:
            # Silent: collected for validation; replayed below only if it passes.
            raw, accepted_pieces, retry_stats = await _collect(
                build_retry_messages(messages, raw, correction)
            )
        except Exception as exc:  # noqa: BLE001 - surfaced below, not swallowed
            _account(None)
            logger.exception("retry after a rejected answer failed")
            return _telemetry(
                answer=GENERATION_FAILED_ANSWER,
                citations=[],
                has_sufficient_evidence=False,
                error=f"retry after rejection failed: {exc}",
                outcome="retry-provider-error",
            )
        _account(retry_stats)

        if not raw.strip() or NOT_IN_DOCUMENTS in raw:
            # Whether this decline is a Stage 4 miss or a Stage 5 failure depends
            # entirely on why the first attempt was rejected, and guessing wrong
            # buries a real defect. If the first answer contradicted a governing
            # rule we measured in the evidence, then the gate demonstrably
            # opened, the corpus does cover the question, and "couldn't find this
            # in the documents" is simply false - it also makes a Stage 5 failure
            # look like a Stage 4 recall miss to verify_generation, which is how
            # the business-class fault was reported as a retrieval problem when it
            # was neither. For any other rejection the existing reading stands: a
            # cross-encoder false positive is a genuine "not in the documents".
            if find_contradiction(query, first_attempt, chunks) is not None:
                logger.info(
                    "retry declined after contradicting the evidence; reporting a "
                    "generation failure rather than a retrieval miss"
                )
                _log_summary("declined-after-contradiction")
                return _telemetry(
                    raw_answer=raw,
                    answer=GENERATION_REJECTED_ANSWER,
                    citations=[],
                    has_sufficient_evidence=False,
                    error=(
                        "retry declined after the answer contradicted its evidence"
                    ),
                    outcome="declined-after-contradiction",
                )
            logger.info("retry declined instead of answering; treating as no evidence")
            _log_summary("declined")
            return _telemetry(
                raw_answer=raw,
                answer=NO_EVIDENCE_ANSWER,
                citations=[],
                has_sufficient_evidence=False,
                outcome="declined",
            )

        citations = build_citations(raw, chunks, query)
        ok, reason, correction = judge_answer(raw, chunks, len(citations), query)
        if not ok:
            # Both attempts failed. Surfacing the second one would show the user a
            # block restatement dressed as an answer, which for a policy tool is
            # worse than admitting the documents did not yield a usable answer.
            # The refusal names the real cause so it is not mistaken for "not in
            # the documents", and the reason is on the state and in the log.
            logger.error(
                "answer still unacceptable after one retry (%s) for %r; "
                "returning no-evidence rather than a restatement",
                reason,
                query,
            )
            _log_summary("rejected-after-retry")
            return _telemetry(
                raw_answer=raw,
                answer=GENERATION_REJECTED_ANSWER,
                citations=[],
                has_sufficient_evidence=False,
                error=f"answer rejected after one retry: {reason}",
                outcome="rejected-after-retry",
            )
        logger.info("retry produced a usable answer: %s", reason)
        _log_summary("answered-after-retry")
        # Replay the retry's pieces to the sink: the first attempt never touched
        # it so the client has seen nothing yet.
        if sink is not None:
            await _replay(accepted_pieces, sink)
        return _telemetry(
            raw_answer=raw,
            answer=strip_markers(raw),
            citations=citations,
            outcome="answered-after-retry",
        )

    # First attempt was accepted. Replay its pieces so the client receives
    # incremental tokens from the validated answer.
    _log_summary("answered")
    if sink is not None:
        await _replay(first_pieces, sink)
    return _telemetry(
        raw_answer=raw,
        answer=strip_markers(raw),
        citations=citations,
        outcome="answered",
    )


def route_after_evidence(state: RAGState) -> str:
    """Conditional edge: generate only when the gate opened."""
    return "generate_answer" if state.get("has_sufficient_evidence") else END


def build_graph() -> Any:
    """Compile the pipeline. Called once at import of the chat service."""
    graph = StateGraph(RAGState)
    graph.add_node("retrieve_hybrid", retrieve_hybrid_node)
    graph.add_node("rerank", rerank_node)
    graph.add_node("check_evidence", check_evidence_node)
    graph.add_node("generate_answer", generate_answer_node)

    graph.add_edge(START, "retrieve_hybrid")
    graph.add_edge("retrieve_hybrid", "rerank")
    graph.add_edge("rerank", "check_evidence")
    graph.add_conditional_edges(
        "check_evidence",
        route_after_evidence,
        {"generate_answer": "generate_answer", END: END},
    )
    graph.add_edge("generate_answer", END)
    return graph.compile()


_compiled: Any | None = None


def get_graph() -> Any:
    global _compiled
    if _compiled is None:
        _compiled = build_graph()
    return _compiled


async def run_answer(
    query: str, document_ids: list | None = None
) -> RAGState:
    """Run the pipeline to completion and return the final state."""
    return await get_graph().ainvoke(
        {"query": query, "document_ids": document_ids}
    )


async def stream_answer(
    query: str, document_ids: list | None = None
) -> AsyncIterator[tuple[str | None, RAGState | None]]:
    """Run the pipeline, yielding ``(piece, None)`` as each token arrives.

    Yields ``(None, final_state)`` exactly once at the end, after the graph has
    finished. A short-circuited run yields only the final tuple - the refusal is
    *not* streamed as tokens, because it never came from a model, and streaming
    it would misrepresent it as generated text. The caller decides how to
    present it.

    The producer is cancelled if the consumer stops early, so a disconnected
    browser does not leave a CPU-bound generation running.
    """
    sink: asyncio.Queue = asyncio.Queue()
    initial: RAGState = {
        "query": query,
        "document_ids": document_ids,
        "token_sink": sink,
    }

    async def produce() -> RAGState:
        try:
            return await get_graph().ainvoke(initial)
        finally:
            await sink.put(None)

    task = asyncio.create_task(produce())
    try:
        while True:
            piece = await sink.get()
            if piece is None:
                break
            yield piece, None
        yield None, await task
    finally:
        if not task.done():
            task.cancel()
