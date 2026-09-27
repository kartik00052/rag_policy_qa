"""Latency breakdown for answer generation, from the provider's own numbers.

Wall-clock per question is the symptom; it cannot say where the time went.
Ollama times every request server-side and returns the breakdown on the final
``done`` event of ``/api/chat`` - ``load_duration``, ``prompt_eval_count``,
``prompt_eval_duration``, ``eval_count``, ``eval_duration`` and
``total_duration``, all nanoseconds. This script asks the real pipeline
(``/api/v1/_debug/ask``) for the same question twice and prints that breakdown
per attempt, so the cost of a first attempt and of a retry are separate numbers
rather than one average.

Each question is asked twice deliberately. The first request of a session pays a
model load, and a load that does not reappear on the *second* request was a cold
start rather than an eviction problem - the distinction decides whether
``keep_alive`` is worth setting at all.

Run against a live backend:

    uv run python -m scripts.measure_llm_latency
    uv run python -m scripts.measure_llm_latency --repeat 3
    uv run python -m scripts.measure_llm_latency --question "What is the nightly accommodation cap in New York?"
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

import httpx

from app.core.eventloop import configure_event_loop

BASE = "http://127.0.0.1:8000"

#: Three questions that between them cover the three shapes of request:
#: a numeric lookup that answers in one sentence, a two-part question that has to
#: state a cap and its exception, and the polar yes/no question that the
#: contradiction check exists for and that has historically needed a retry.
QUESTIONS: list[str] = [
    "What is the nightly accommodation cap in New York?",
    "How much annual leave do I accrue each month, and what is the cap?",
    "Can I book business class for a five hour flight?",
]


def _fmt(value: float | None, suffix: str = "") -> str:
    if value is None:
        return "n/a"
    return f"{value:.2f}{suffix}"


def _timing_line(stats: dict[str, Any]) -> str:
    """One attempt as a single readable line of measured values."""
    return (
        f"prompt={stats['prompt_eval_count']:>4} tok "
        f"prefill={_fmt(stats['prompt_eval_seconds'])}s "
        f"({stats['prompt_tokens_per_second']:.0f} tok/s)  "
        f"eval={stats['eval_count']:>4} tok "
        f"decode={_fmt(stats['eval_seconds'])}s "
        f"({stats['tokens_per_second']:.1f} tok/s)  "
        f"load={_fmt(stats['load_seconds'])}s  "
        f"total={_fmt(stats['total_seconds'])}s  "
        f"reason={stats['done_reason'] or 'n/a'}"
    )


async def ask(client: httpx.AsyncClient, query: str) -> dict[str, Any]:
    response = await client.post(
        f"{BASE}/api/v1/_debug/ask", json={"query": query}, timeout=600.0
    )
    response.raise_for_status()
    return response.json()


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeat", type=int, default=2, help="ask each question N times")
    parser.add_argument("--question", action="append", default=None, help="ask one question")
    args = parser.parse_args()

    questions = args.question or QUESTIONS
    totals: list[float] = []
    first_attempt_totals: list[float] = []
    loads: list[float] = []
    prefills: list[float] = []
    decodes: list[float] = []
    decoded_tokens: list[int] = []
    prompt_tokens: list[int] = []
    retried = 0
    answered = 0

    print("=" * 78)
    print(f"Generation latency breakdown against {BASE}")
    print("=" * 78)

    async with httpx.AsyncClient() as client:
        for query in questions:
            for iteration in range(1, args.repeat + 1):
                row = await ask(client, query)
                stats = row["attempt_stats"]
                is_cold = iteration == 1
                print(f"\nQ: {query}   [request {iteration}/{args.repeat}]")
                print(
                    f"   outcome={row['outcome']} attempts={row['attempts']} "
                    f"wall={row['wall_ms'] / 1000:.2f}s "
                    f"graph_only={row['graph_ms'] / 1000:.2f}s "
                    f"chunks={row['context']['chunks']} "
                    f"prompt_chars={row['context']['prompt_chars']}"
                    + ("   <- cold start" if is_cold else "")
                )
                for index, attempt in enumerate(stats, start=1):
                    print(
                        f"   attempt {index}: {_timing_line(attempt)}"
                    )
                if row["has_sufficient_evidence"]:
                    print(f"   A: {row['answer'][:220]!r}")
                else:
                    print(f"   A: {row['answer'][:220]!r}")
                if row["error"]:
                    print(f"   error: {row['error']}")

                answered += 1
                retried += 1 if row["retried"] else 0
                totals.append(row["wall_ms"] / 1000)
                if stats:
                    first_attempt_totals.append(stats[0]["total_seconds"])
                    loads.append(stats[0]["load_seconds"])
                    prefills.append(stats[0]["prompt_eval_seconds"])
                    prompt_tokens.append(stats[0]["prompt_eval_count"])
                for attempt in stats:
                    decodes.append(attempt["eval_seconds"])
                    decoded_tokens.append(attempt["eval_count"])

    def mean(values: list[float]) -> float:
        return sum(values) / len(values) if values else 0.0

    print("\n" + "=" * 78)
    print("SUMMARY")
    print("=" * 78)
    print(f"  requests measured            : {answered}")
    print(f"  avg wall-clock per question   : {mean(totals):.2f}s")
    print(f"  avg first-attempt model time  : {mean(first_attempt_totals):.2f}s")
    print(f"  avg load_duration             : {mean(loads) * 1000:.1f}ms")
    print(f"  avg prefill (prompt_eval)     : {mean(prefills):.2f}s")
    print(f"  avg decode (eval)             : {mean(decodes):.2f}s")
    print(f"  avg prompt tokens             : {mean(prompt_tokens):.0f}")
    print(
        f"  avg generated tokens          : "
        f"{mean([float(t) for t in decoded_tokens]):.0f}"
    )
    if decoded_tokens:
        decode_seconds = mean(decodes)
        tps = mean([float(t) for t in decoded_tokens]) / decode_seconds if decode_seconds else 0
        print(f"  decode throughput             : {tps:.1f} tok/s")
    print(f"  requests that retried         : {retried}/{answered}")
    print("=" * 78)
    print(json.dumps({
        "requests": answered,
        "avg_wall_s": round(mean(totals), 2),
        "avg_first_attempt_model_s": round(mean(first_attempt_totals), 2),
        "avg_load_ms": round(mean(loads) * 1000, 1),
        "avg_prefill_s": round(mean(prefills), 2),
        "avg_decode_s": round(mean(decodes), 2),
        "avg_prompt_tokens": round(mean(prompt_tokens)),
        "avg_generated_tokens": round(mean([float(t) for t in decoded_tokens])),
        "retried": retried,
    }))
    return 0


if __name__ == "__main__":
    configure_event_loop()
    sys.exit(asyncio.run(main()))
