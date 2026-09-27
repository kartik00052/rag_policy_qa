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

* **Tokens are streamed live; a rejected attempt is superseded, not hidden.** The
  first generation streams to the client as it arrives, because PROJECT.md
  Section 6 and the Stage 6 checkpoint both require incremental tokens and
  buffering the whole answer to validate it first threw that away. If the
  validator then rejects the answer, the retry runs *silently* and the ``done``
  event's ``answer`` carries the corrected text. A token already sent cannot be
  recalled, so a rejected first attempt is briefly visible as a preview that the
  final event replaces. That is the deliberate trade: the authoritative answer is
  always validated, and 16 of 18 measured answers needed no retry at all, so the
  preview is the common path rather than the exception. The alternative -
  validate before sending anything - was measured and rejected because it made
  every answer arrive as a burst, breaking documented Stage 6 behaviour for a
  fault that affects a minority of answers. Clients must therefore treat
  ``done.answer`` as authoritative over accumulated tokens; the SSE contract
  already carries the full text there.
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
from app.rag.prompts import (
    NOT_IN_DOCUMENTS,
    build_context,
    build_messages,
    build_retry_messages,
)
from app.rag.restatement import MAX_OVERLAP, worst_overlap
from app.rag.state import RAGState
from app.services.evidence import NO_EVIDENCE_ANSWER, assess
from app.services.llm import get_llm
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
    raw: str, chunks: list, citation_count: int
) -> tuple[bool, str, str]:
    """Decide whether a generated answer is a real answer.

    Returns ``(ok, reason, correction)``. ``reason`` names the fault and is logged;
    ``correction`` is the retry instruction, which differs by fault because a
    "you cited nothing" answer needs a different nudge than a verbatim copy.

    Three faults, all observed with this model on the sample policy:

    * the model emitted no citation marker at all - it referenced its source in
      prose ("this can be found in Table 6.2a of Appendix A") and left the user
      with claims they could not check;
    * a numbered block restatement;
    * verbatim overlap at or above :data:`~app.rag.restatement.MAX_OVERLAP` with
      any single block.

    Deliberately keyed on markers in the model's own text, not on
    ``citation_count``. A marker can be emitted and then dropped by
    :func:`~app.rag.citations.build_citations` because the block it names scored
    below the evidence threshold; that is the citation layer declining to vouch
    for a weak block, not the model failing to cite, and retrying cannot fix it
    because the same block will be dropped again. Judging on the filtered list
    turned a correct, fully-marked answer into a hard refusal.
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
    messages: list[dict[str, str]], sink: asyncio.Queue | None = None
) -> str:
    """Run one generation, returning the joined text.

    When ``sink`` is given, each piece is forwarded the moment it arrives so the
    client sees incremental tokens. Pass ``None`` for a generation that must stay
    silent - see :func:`generate_answer_node`.
    """
    pieces: list[str] = []
    async for piece in get_llm().stream(messages):
        pieces.append(piece)
        if sink is not None:
            await sink.put(piece)
    return "".join(pieces)


async def generate_answer_node(state: RAGState) -> dict[str, Any]:
    """Stage 5 generation. Only reached when the gate opened."""
    settings = get_settings()
    chunks = state.get("reranked_chunks") or []
    sink = state.get("token_sink")
    messages = build_messages(state["query"], chunks)

    try:
        raw = await _collect(messages, sink)
    except Exception as exc:  # noqa: BLE001 - surfaced below, not swallowed
        logger.exception(
            "generation failed for model %s", settings.ollama_model
        )
        return {
            "answer": GENERATION_FAILED_ANSWER,
            "citations": [],
            "has_sufficient_evidence": False,
            "error": str(exc),
        }

    if not raw.strip():
        logger.warning("provider returned an empty answer for %r", state["query"])
        return {
            "answer": GENERATION_FAILED_ANSWER,
            "citations": [],
            "has_sufficient_evidence": False,
            "error": "provider returned an empty answer",
        }

    # The model can decline even when the gate opened - it read the blocks and
    # found no answer, which is the cross-encoder false-positive case (a passage
    # about the right topic that does not answer the actual question). That is
    # a refusal, not a failure, so it is reported the same way the gate's
    # refusal is: no citations, and not enough evidence.
    if NOT_IN_DOCUMENTS in raw:
        logger.info("model declined after the gate opened; treating as no evidence")
        return {
            "raw_answer": raw,
            "answer": NO_EVIDENCE_ANSWER,
            "citations": [],
            "has_sufficient_evidence": False,
        }

    citations = build_citations(raw, chunks, state["query"])
    ok, reason, correction = judge_answer(raw, chunks, len(citations))
    if not ok:
        logger.warning(
            "generated answer rejected (%s) for %r; retrying once with a correction",
            reason,
            state["query"],
        )
        try:
            # No sink: the retry must not append to text the client already has.
            raw = await _collect(build_retry_messages(messages, raw, correction))
        except Exception as exc:  # noqa: BLE001 - surfaced below, not swallowed
            logger.exception("retry after a rejected answer failed")
            return {
                "answer": GENERATION_FAILED_ANSWER,
                "citations": [],
                "has_sufficient_evidence": False,
                "error": f"retry after rejection failed: {exc}",
            }

        if not raw.strip() or NOT_IN_DOCUMENTS in raw:
            logger.info("retry declined instead of answering; treating as no evidence")
            return {
                "raw_answer": raw,
                "answer": NO_EVIDENCE_ANSWER,
                "citations": [],
                "has_sufficient_evidence": False,
            }

        citations = build_citations(raw, chunks, state["query"])
        ok, reason, correction = judge_answer(raw, chunks, len(citations))
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
                state["query"],
            )
            return {
                "raw_answer": raw,
                "answer": GENERATION_REJECTED_ANSWER,
                "citations": [],
                "has_sufficient_evidence": False,
                "error": f"answer rejected after one retry: {reason}",
            }
        logger.info("retry produced a usable answer: %s", reason)

    # The pieces of the *validated* generation are already on the sink for the
    # common case (no rejection happened, so attempt 1 was the validated one). When
    # a rejection did happen the retry ran silently, so nothing is pushed here: the
    # `done` event's `answer` is authoritative and carries the corrected text.
    return {
        "raw_answer": raw,
        "answer": strip_markers(raw),
        "citations": citations,
    }


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
