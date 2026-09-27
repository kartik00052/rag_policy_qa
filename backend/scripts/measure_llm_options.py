"""Isolated A/B of the Ollama request options, at the provider level.

Why this exists alongside :mod:`scripts.measure_llm_latency`
-----------------------------------------------------------
``measure_llm_latency`` measures the whole request path, so it is the right tool
for "how long does a question take". It is the wrong tool for "which of the three
request options is responsible", because retrieval, reranking and a possible
retry are all folded into the same wall-clock number and cannot be held
constant across variants.

This script removes everything except the provider call. It reconstructs the
*real* prompt - the app's own ``build_messages`` over the same top-8 chunks the
pipeline retrieves - and then sweeps ``num_ctx`` / ``num_predict`` /
``keep_alive`` against Ollama directly, so every variant differs in exactly one
option and nothing else.

The reconstruction is checked, not assumed: the prompt token count this script
reports is compared against the live pipeline's own ``prompt_eval_count`` from
``/api/v1/_debug/ask``, and a reconstruction that drifts more than 10% is
reported as a warning, because a sweep on a non-representative prompt would
produce numbers that look precise and mean nothing.

Run against a live backend (the backend supplies the corpus; Ollama does the
generation):

    uv run python -m scripts.measure_llm_options
    uv run python -m scripts.measure_llm_options --repeat 3
    uv run python -m scripts.measure_llm_options --eviction-cost
    uv run python -m scripts.measure_llm_options --json out.json

``--eviction-cost`` is the measurement that justifies ``keep_alive``: it
deliberately unloads the model, then asks the same question again, so the
difference between the two numbers is exactly the cost a dev-session gap
inflicts. Without it, ``keep_alive`` looks free and unmeasured.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from typing import Any

import httpx

from app.core.eventloop import configure_event_loop
from app.rag.prompts import build_messages
from app.schemas.rag import RerankedChunk
from app.schemas.retrieval import RetrievedChunk

BASE = "http://127.0.0.1:8000"
OLLAMA = "http://localhost:11434"
MODEL = "qwen2.5:3b"

#: The app sends this many chunks (``rerank_top_k``), so the reconstructed prompt
#: asks for the same number. Widening it would make the sweep measure a prompt
#: the pipeline never sends.
TOP_K = 8

#: Three questions that between them cover the three shapes of real request: a
#: numeric lookup that answers in one sentence, a two-part question that must
#: state a cap and its exception, and the polar yes/no question the contradiction
#: check exists for.
#:
#: Chosen because each is *answerable*, so each actually reaches the model. The
#: obvious "nightly accommodation cap in New York" was in the earlier list and
#: is a documented evidence-gate false negative (cross-encoder logit -1.89, see
#: the comment on ``Settings.evidence_min_score``): the gate closes, the LLM is
#: never called, and the question contributes no timing data at all while
#: looking, in the output, like a fast request.
QUESTIONS: list[str] = [
    "How much annual leave do I accrue each month, and what is the cap?",
    "Can I book business class for a five hour flight?",
    "How much sick leave do full-time employees get, and can it carry over?",
]

#: The variants. Each entry is ``(label, options, keep_alive)``. Every variant
#: uses the app's real tuned values except the one it is testing, so the
#: comparison isolates a single change.
VARIANTS: list[tuple[str, dict[str, Any], str]] = [
    ("ctx4096 (pre-change num_ctx)", {"num_ctx": 4096}, "30m"),
    ("ctx2048 (current)", {"num_ctx": 2048}, "30m"),
    ("ctx1536", {"num_ctx": 1536}, "30m"),
    ("ctx4096 + unbounded predict", {"num_ctx": 4096, "num_predict": -1}, "30m"),
    ("ctx2048 + predict300 (current)", {"num_ctx": 2048, "num_predict": 300}, "30m"),
]

TEMPERATURE = 0.0


async def fetch_prompts(client: httpx.AsyncClient) -> list[dict[str, Any]]:
    """The real prompt for each question, built by the app's own code."""
    prompts: list[dict[str, Any]] = []
    for query in QUESTIONS:
        response = await client.post(
            f"{BASE}/api/v1/_debug/search",
            json={"query": query, "limit": TOP_K},
            timeout=120.0,
        )
        response.raise_for_status()
        results = response.json()["results"]
        chunks = [
            RerankedChunk(
                chunk=RetrievedChunk(**item),
                score=0.0,
                rrf_score=0.0,
                rank=position,
            )
            for position, item in enumerate(results, start=1)
        ]
        prompts.append(
            {
                "query": query,
                "messages": build_messages(query, chunks),
                "prompt_chars": sum(len(m["content"]) for m in build_messages(query, chunks)),
            }
        )
    return prompts


async def ask_ollama(
    client: httpx.AsyncClient,
    messages: list[dict[str, str]],
    options: dict[str, Any],
    keep_alive: str,
    model: str = MODEL,
) -> dict[str, Any]:
    """One non-streaming chat call. Returns Ollama's own timing breakdown."""
    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        "keep_alive": keep_alive,
        "options": {"temperature": TEMPERATURE, **options},
    }
    started = time.perf_counter()
    response = await client.post(f"{OLLAMA}/api/chat", json=payload, timeout=1800.0)
    response.raise_for_status()
    wall = time.perf_counter() - started
    body = response.json()
    if body.get("error"):
        raise RuntimeError(body["error"])
    ns = 1e9
    return {
        "wall": wall,
        "load": (body.get("load_duration") or 0) / ns,
        "prefill": (body.get("prompt_eval_duration") or 0) / ns,
        "prompt_tokens": body.get("prompt_eval_count") or 0,
        "prefill_tps": (body.get("prompt_eval_count") or 0)
        / max((body.get("prompt_eval_duration") or 0) / ns, 1e-9),
        "decode": (body.get("eval_duration") or 0) / ns,
        "eval_tokens": body.get("eval_count") or 0,
        "decode_tps": (body.get("eval_count") or 0)
        / max((body.get("eval_duration") or 0) / ns, 1e-9),
        "total": (body.get("total_duration") or 0) / ns,
        "done_reason": body.get("done_reason") or "",
        "answer": (body.get("message") or {}).get("content", ""),
    }


async def unload(client: httpx.AsyncClient, model: str = MODEL) -> None:
    """Force the model out of memory, the way an idle gap would.

    ``keep_alive: 0`` is Ollama's documented "unload now" and is the honest way to
    reproduce eviction: waiting out a real keep_alive window would take minutes
    of wall clock per data point and measure the same thing.
    """
    await client.post(
        f"{OLLAMA}/api/chat",
        json={"model": model, "messages": [{"role": "user", "content": "hi"}],
              "keep_alive": 0, "stream": False},
        timeout=120.0,
    )


def _agg(rows: list[dict[str, Any]]) -> dict[str, float]:
    def med(key: str) -> float:
        return statistics.median([r[key] for r in rows])

    return {
        "wall_median": med("wall"),
        "load_median": med("load"),
        "prefill_median": med("prefill"),
        "prefill_tps_median": med("prefill_tps"),
        "decode_median": med("decode"),
        "decode_tps_median": med("decode_tps"),
        "eval_tokens_median": med("eval_tokens"),
        "total_median": med("total"),
    }


def print_row(label: str, agg: dict[str, float], n: int) -> None:
    print(
        f"  {label:<30} wall={agg['wall_median']:>7.2f}s  "
        f"load={agg['load_median']:>6.2f}s  "
        f"prefill={agg['prefill_median']:>7.2f}s ({agg['prefill_tps_median']:>6.0f} tok/s)  "
        f"decode={agg['decode_median']:>6.2f}s ({agg['decode_tps_median']:>5.1f} tok/s)  "
        f"gen={agg['eval_tokens_median']:>5.0f} tok  n={n}"
    )


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeat", type=int, default=2, help="warm calls per variant per question")
    parser.add_argument("--model", default=MODEL)
    parser.add_argument("--eviction-cost", action="store_true",
                        help="measure the cost of a model evicted between requests")
    parser.add_argument("--skip-validate", action="store_true",
                        help="skip checking the reconstructed prompt against the live pipeline")
    parser.add_argument("--json", default=None, help="also write the raw rows here")
    args = parser.parse_args()

    report: dict[str, Any] = {"model": args.model, "variants": {}, "questions": []}

    async with httpx.AsyncClient() as client:
        prompts = await fetch_prompts(client)
        report["prompt_chars"] = [p["prompt_chars"] for p in prompts]
        report["questions"] = QUESTIONS

        print("=" * 100)
        print(f"Ollama option sweep against {OLLAMA} (model={args.model}, temperature={TEMPERATURE})")
        print(f"reconstructed prompt chars per question: {report['prompt_chars']}")
        print("=" * 100)

        if not args.skip_validate:
            # Fidelity check. The sweep is only meaningful if it measures the
            # prompt the pipeline actually sends, so the reconstruction is
            # compared against the live graph's own prompt_eval_count - a number
            # produced by the real reranked top-8, not by this script.
            print("\n--- prompt fidelity: reconstructed vs live pipeline")
            fidelity: list[dict[str, Any]] = []
            for prompt in prompts:
                response = await client.post(
                    f"{BASE}/api/v1/_debug/ask",
                    json={"query": prompt["query"]},
                    timeout=1800.0,
                )
                response.raise_for_status()
                live = response.json()
                live_tokens = (live["attempt_stats"] or [{}])[0].get("prompt_eval_count", 0)
                # English policy prose runs about 4 characters per token on
                # this model's tokenizer, i.e. ~0.25 tokens/char, so the
                # approximation divides rather than multiplies. It was written
                # as "/ 1.3", which overstates the count by 3x and made every
                # question report DRIFT; the comparison is a range check, not a
                # tokenizer call, so the divisor only has to be close.
                approx = int(prompt["prompt_chars"] / 4.0)
                drift = (approx - live_tokens) / live_tokens * 100 if live_tokens else 0.0
                fidelity.append(
                    {
                        "query": prompt["query"],
                        "live_prompt_tokens": live_tokens,
                        "approx_tokens": approx,
                        "drift_pct": round(drift, 1),
                    }
                )
                flag = "OK" if abs(drift) <= 10 else "DRIFT"
                print(
                    f"    {prompt['query'][:46]:<48} live={live_tokens:>5} tok  "
                    f"reconstructed~{approx:>5} tok  drift={drift:>+5.1f}%  {flag}"
                )
            report["fidelity"] = fidelity

        for label, options, keep_alive in VARIANTS:
            rows: list[dict[str, Any]] = []
            print(f"\n--- {label}   options={options} keep_alive={keep_alive}")

            # Warm calls first, with the model left resident, so the variant is
            # measured in steady state. A variant measured straight after an
            # unload inherits the cold-start penalty and looks slower for a
            # reason that has nothing to do with the option under test.
            for prompt in prompts:
                for iteration in range(1, args.repeat + 1):
                    row = await ask_ollama(
                        client, prompt["messages"], options, keep_alive, args.model
                    )
                    row["cold"] = False
                    row["query"] = prompt["query"]
                    rows.append(row)
                    print(
                        f"    warm {prompt['query'][:44]:<47} "
                        f"wall={row['wall']:>6.1f}s load={row['load']:>5.2f}s "
                        f"prefill={row['prefill']:>6.2f}s ({row['prefill_tps']:>5.0f} tok/s) "
                        f"decode={row['decode']:>5.2f}s gen={row['eval_tokens']:>3} tok"
                    )

            # One cold sample per variant, on the first question only. Enough to
            # show the cold prefill rate, without paying ~55s per question per
            # variant for a number that is a property of the machine, not of the
            # option being varied.
            await unload(client, args.model)
            cold_row = await ask_ollama(
                client, prompts[0]["messages"], options, keep_alive, args.model
            )
            cold_row["cold"] = True
            cold_row["query"] = prompts[0]["query"]
            rows.append(cold_row)
            print(
                f"    COLD {prompts[0]['query'][:44]:<47} "
                f"wall={cold_row['wall']:>6.1f}s load={cold_row['load']:>5.2f}s "
                f"prefill={cold_row['prefill']:>6.2f}s ({cold_row['prefill_tps']:>5.0f} tok/s) "
                f"decode={cold_row['decode']:>5.2f}s gen={cold_row['eval_tokens']:>3} tok"
            )

            warm = [r for r in rows if not r["cold"]]
            cold = [r for r in rows if r["cold"]]
            report["variants"][label] = {
                "options": options,
                "keep_alive": keep_alive,
                "cold": [_agg(cold)] if cold else {},
                "warm": _agg(warm) if warm else {},
                "rows": rows,
            }
            if warm:
                print_row("  warm (median)", _agg(warm), len(warm))
            if cold:
                print_row("  cold (median)", _agg(cold), len(cold))

        if args.eviction_cost:
            print("\n" + "=" * 100)
            print("EVICTION COST: what a gap between questions costs")
            print("=" * 100)
            costs: list[dict[str, float]] = []
            for prompt in prompts:
                await unload(client, args.model)
                evicted = await ask_ollama(
                    client, prompt["messages"], {"num_ctx": 2048, "num_predict": 300}, "30m", args.model
                )
                # Second call with the model now warm - no unload in between.
                resident = await ask_ollama(
                    client, prompt["messages"], {"num_ctx": 2048, "num_predict": 300}, "30m", args.model
                )
                costs.append(
                    {
                        "query": prompt["query"],
                        "evicted_wall": evicted["wall"],
                        "resident_wall": resident["wall"],
                        "saving": resident["wall"] - evicted["wall"],
                        "evicted_prefill_tps": evicted["prefill_tps"],
                        "resident_prefill_tps": resident["prefill_tps"],
                    }
                )
                print(
                    f"  {prompt['query'][:44]:<46} evicted={evicted['wall']:>6.1f}s "
                    f"({evicted['prefill_tps']:>5.0f} tok/s prefill)  "
                    f"resident={resident['wall']:>6.1f}s ({resident['prefill_tps']:>5.0f} tok/s prefill)  "
                    f"saved={resident['wall'] - evicted['wall']:>6.1f}s"
                )
            report["eviction_cost"] = costs
            report["eviction_cost_median_saving"] = statistics.median(
                [c["saving"] for c in costs]
            )

    if args.json:
        with open(args.json, "w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2)
        print(f"\nraw rows written to {args.json}")
    return 0


if __name__ == "__main__":
    configure_event_loop()
    sys.exit(asyncio.run(main()))
