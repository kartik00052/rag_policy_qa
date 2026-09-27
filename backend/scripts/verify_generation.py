"""Verify answer synthesis against the sample document (Stage 5).

:mod:`scripts.verify_injection` proves the injection safeguard holds. It does not
prove the model actually *answered* - it asks one fixed question against its own
adversarial fixture, so a model that paraphrases well but a model that restates
a block both "pass" as long as no injected string comes back.

This script covers the gap. It asks a spread of real questions about the sample
policy, and for each one asserts the answer:

* is not a refusal or a generation failure,
* carries at least one citation,
* is not a bare ``(1) (2) <text>`` block restatement,
* stays under a verbatim-overlap ceiling against every retrieved block,
* actually contains the facts the sample document states.

The overlap ceiling is the interesting one. A model that keeps its citations but
reproduces a block word-for-word satisfies every citation-shaped check, so the
copying has to be measured. See :mod:`scripts.restatement` for the metric and the
justification for the shingle size; ``MAX_OVERLAP`` below carries the calibrated
value and the reasoning.

Run against a live server:

    uv run python -m scripts.verify_generation
    uv run python -m scripts.verify_generation --runs 5
    uv run python -m scripts.verify_generation --max-overlap 0.20
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from typing import Any

import httpx

from app.core.eventloop import configure_event_loop
from app.rag.graph import GENERATION_REJECTED_ANSWER
from app.rag.restatement import MAX_OVERLAP, RUN_DIAGNOSTIC_WORDS, worst_overlap

BASE = "http://127.0.0.1:8000"

#: Ceiling on 8-gram containment between the answer and any single retrieved
#: block.
#:
#: Imported from the app rather than redefined, so the number this script asserts
#: is the same number the request path enforces. A test that hardcoded its own
#: copy would drift the moment one side was retuned. The calibration - genuine
#: synthesised answers measured 0.00-0.245, confirmed restatements 0.41-1.00, and
#: the ceiling sits at 0.30 in the gap - is documented in
#: :mod:`app.rag.restatement`.

#: The bare-block-restatement shape seen from qwen2.5:3b: numbered items whose
#: text is the source block, with no citations.
_NUMBERED_RESTATEMENT = re.compile(r"^\s*\(\d+\)")

#: Real questions against the sample policy, with the facts the document states.
#: ``expect`` substrings must all appear (case-insensitively) in the answer.
QUESTIONS: list[tuple[str, list[str]]] = [
    (
        "How much annual leave do I accrue each month, and what is the cap?",
        ["1.75", "25"],
    ),
    (
        "What is the nightly accommodation cap in New York?",
        ["1500"],
    ),
    (
        "Can I book business class for a five hour flight?",
        ["economy"],
    ),
    (
        "What is the daily meal allowance for an international trip?",
        ["100"],
    ),
    (
        "How long do I have to submit an expense claim after a trip?",
        ["30"],
    ),
    (
        "How much parental leave does the secondary caregiver get?",
        ["6 week"],
    ),
    (
        "What is the daily cap on taxi and rideshare fares?",
        ["120"],
    ),
    (
        "Who must approve travel, and how far in advance?",
        ["manager", "5 working days"],
    ),
    (
        "How much sick leave do full-time employees get, and can it carry over?",
        ["10", "5"],
    ),
    (
        "Do contractors get annual leave under this policy?",
        ["contractor"],
    ),
]


class Report:
    """Accumulates per-question results so a run's evidence is all on screen."""

    def __init__(self) -> None:
        self.checks = 0
        self.failures: list[str] = []
        self.skips: list[str] = []
        self.rows: list[dict[str, Any]] = []

    def check(self, condition: bool, label: str, detail: str = "") -> bool:
        self.checks += 1
        if not condition:
            self.failures.append(label if not detail else f"{label} -- {detail}")
        return condition

    def skip(self, label: str, detail: str = "") -> None:
        """Recorded separately from failures, and printed just as loudly.

        A gate decline short-circuits before the LLM is invoked (see the
        ``check_evidence -> END`` edge), so nothing about generation quality was
        tested. Scoring it as a generation failure would blame Stage 5 for a
        Stage 4 recall miss, and silently dropping it would hide a real retrieval
        problem. It is neither.
        """
        self.skips.append(label if not detail else f"{label} -- {detail}")

    def summarize(self) -> int:
        passed = self.checks - len(self.failures)
        print("\n" + "=" * 78)
        print(f"{passed}/{self.checks} checks passed")
        if self.failures:
            print("FAILURES:")
            for failure in self.failures:
                print(f"  - {failure}")
        if self.skips:
            print("SKIPPED (evidence gate declined; LLM never invoked):")
            for skip in self.skips:
                print(f"  - {skip}")
        print("=" * 78)
        return 1 if self.failures else 0


async def stream_chat(
    client: httpx.AsyncClient, query: str
) -> tuple[list[str], dict[str, Any] | None]:
    tokens: list[str] = []
    final: dict[str, Any] | None = None
    event: str | None = None
    async with client.stream(
        "POST", f"{BASE}/api/v1/chat/stream", json={"query": query}, timeout=300.0
    ) as response:
        response.raise_for_status()
        async for line in response.aiter_lines():
            if line.startswith("event:"):
                event = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                body = json.loads(line.split(":", 1)[1].strip())
                if event == "token":
                    tokens.append(body["text"])
                elif event == "done":
                    final = body
    return tokens, final


async def retrieved_blocks(client: httpx.AsyncClient, query: str) -> list[str]:
    """Source text the answer could have been copied from.

    Taken from the hybrid search the pipeline runs, widened to 10 so a block that
    the reranker demoted is still available as a comparison target - the check
    should catch copying of anything retrieved, not only of what survived into
    the prompt.
    """
    response = await client.post(
        f"{BASE}/api/v1/_debug/search",
        json={"query": query, "limit": 10},
        timeout=120.0,
    )
    response.raise_for_status()
    return [r["content"] for r in response.json()["results"]]


async def ask_one(
    client: httpx.AsyncClient, query: str, expect: list[str], max_overlap: float
) -> dict[str, Any]:
    row: dict[str, Any] = {"question": query}
    tokens, final = await stream_chat(client, query)
    if final is None:
        row.update(status="no-final-event", overlap=None, citations=0, answer="")
        return row
    answer = final.get("answer") or ""
    row["answer"] = answer
    row["citations"] = len(final.get("citations") or [])
    row["sufficient"] = bool(final.get("has_sufficient_evidence"))

    if not row["sufficient"]:
        # Both a closed gate and a twice-rejected generation arrive as
        # has_sufficient_evidence=False, and they mean opposite things: the first
        # is a Stage 4 recall miss with nothing to say about generation, the
        # second is a Stage 5 failure. Scoring the second as a skip would quietly
        # exclude the exact defect this suite exists to catch, so the two
        # refusals are told apart by which text the server chose to send.
        row["overlap"] = 0.0
        if answer.strip() == GENERATION_REJECTED_ANSWER.strip():
            row["status"] = "generation-rejected"
        else:
            row["status"] = "gate-declined"
        return row

    blocks = await retrieved_blocks(client, query)
    ratio, block_index, run = worst_overlap(answer, blocks)
    row.update(
        status="answered",
        overlap=round(ratio, 4),
        block=block_index,
        run=run,
        blocks=len(blocks),
    )
    return row


def judge(report: Report, row: dict[str, Any], expect: list[str], max_overlap: float) -> None:
    label = f"[{row['question'][:58]}]"
    status = row["status"]
    if status == "gate-declined":
        report.skip(label, "evidence gate closed before the LLM was called")
        return
    if status == "generation-rejected":
        report.check(
            False,
            f"{label} produced an answer",
            "generation rejected twice; server returned a refusal instead of an answer",
        )
        return
    if status == "no-final-event":
        report.check(False, f"{label} answered from the documents", status)
        return

    answer = row["answer"]
    report.check(bool(answer.strip()), f"{label} produced an answer")
    report.check(
        "couldn't reach the language model" not in answer.lower(),
        f"{label} is not a generation failure",
        answer[:120],
    )
    report.check(
        row["citations"] >= 1,
        f"{label} carries at least one citation",
        f"citations={row['citations']}",
    )
    report.check(
        not _NUMBERED_RESTATEMENT.search(answer),
        f"{label} is not a numbered block restatement",
        answer[:80],
    )
    report.check(
        row["overlap"] < max_overlap,
        f"{label} verbatim overlap under {max_overlap}",
        f"overlap={row['overlap']} vs block {row.get('block')} "
        f"(longest run {row.get('run')} words)",
    )
    missing = [fact for fact in expect if fact.lower() not in answer.lower()]
    report.check(
        not missing,
        f"{label} states the documented facts",
        f"missing {missing} in {answer[:120]!r}",
    )


class _Tee:
    """Write to two streams, so a long run is durable even if it is detached.

    Five runs of this suite takes the better part of half an hour, and a run whose
    only record was a redirected stdout that the launching shell then discarded
    is worth nothing. Flushing on every write keeps the file complete if the
    process is killed part-way.
    """

    def __init__(self, *streams: Any) -> None:
        self._streams = streams

    def write(self, data: str) -> int:
        for stream in self._streams:
            stream.write(data)
            stream.flush()
        return len(data)

    def flush(self) -> None:
        for stream in self._streams:
            stream.flush()


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=1, help="repeat the whole set N times")
    parser.add_argument(
        "--max-overlap", type=float, default=MAX_OVERLAP, help="overlap ceiling"
    )
    parser.add_argument(
        "--question-index", type=int, default=None, help="run a single question"
    )
    parser.add_argument(
        "--log", default=None, help="also append all output to this file"
    )
    args = parser.parse_args()

    if args.log:
        handle = open(args.log, "a", encoding="utf-8", buffering=1)
        sys.stdout = _Tee(sys.__stdout__, handle)

    questions = QUESTIONS
    if args.question_index is not None:
        questions = [QUESTIONS[args.question_index]]

    report = Report()
    print("=" * 78)
    print(f"Generation verification against {BASE}")
    print(
        f"overlap ceiling: {args.max_overlap}   "
        f"run-length diagnostic: >={RUN_DIAGNOSTIC_WORDS} words"
    )
    print("=" * 78)

    async with httpx.AsyncClient() as client:
        for run_number in range(1, args.runs + 1):
            print(f"\n########## RUN {run_number}/{args.runs} ##########")
            for query, expect in questions:
                started = time.monotonic()
                row = await ask_one(client, query, expect, args.max_overlap)
                elapsed = time.monotonic() - started
                print(f"\nQ: {query}")
                print(
                    f"   status={row['status']} citations={row['citations']} "
                    f"overlap={row.get('overlap')} run={row.get('run')} "
                    f"({elapsed:.1f}s)"
                )
                print(f"   A: {row['answer'][:400]!r}")
                judge(report, row, expect, args.max_overlap)
                report.rows.append(row)

    print("\n" + "=" * 78)
    print("PER-QUESTION OVERLAP SUMMARY (worst block per question)")
    print("=" * 78)
    for row in report.rows:
        if row["status"] == "answered":
            print(
                f"  overlap={row['overlap']:.4f} run={row.get('run'):>3} "
                f"cites={row['citations']}  {row['question'][:62]}"
            )
    return report.summarize()


if __name__ == "__main__":
    configure_event_loop()
    sys.exit(asyncio.run(main()))
