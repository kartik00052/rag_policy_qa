"""Verify Stage 6: streaming chat, citations, and conversation persistence.

Exercises the real HTTP surface of a running server (start it with
``uv run python run.py``) rather than calling the graph directly, because the
claim under test is about the API: that tokens arrive incrementally over SSE and
that the final event has exactly the documented shape.

Checks performed:

1. A real question returns ``has_sufficient_evidence: true``, streams more than
   one ``token`` event, and those tokens arrive spread over time rather than in
   one burst (a single-burst response would mean the server buffered).
2. The final event has exactly the keys PROJECT.md Section 6 specifies, and each
   citation carries the documented fields.
3. Every citation's ``matched_text`` is a literal substring of the chunk it came
   from, re-read from Qdrant here rather than trusted from the payload.
4. An irrelevant question returns ``has_sufficient_evidence: false`` and emits no
   token events, proving the refusal came from the gate and not from a model.
5. Conversations, messages and citations are actually in Postgres, queried
   directly.
6. The list and detail conversation endpoints agree with the database.

Run: ``uv run python -m scripts.verify_stage6``
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.core.config import get_settings
from app.core.eventloop import configure_event_loop

BASE = "http://127.0.0.1:8000"

#: Final event shape, from PROJECT.md Section 6.
FINAL_KEYS = {"answer", "has_sufficient_evidence", "citations"}
CITATION_KEYS = {
    "id",
    "document_id",
    "document_name",
    "page",
    "section",
    "matched_text",
    "relevance",
}

ANSWERABLE = "How many days of annual leave do I get per year?"
OFF_TOPIC = "What is the gym membership subsidy?"

failures: list[str] = []
checks = 0


def check(condition: bool, label: str, detail: str = "") -> bool:
    global checks
    checks += 1
    if condition:
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label}" + (f"  -- {detail}" if detail else ""))
        failures.append(label)
    return condition


@dataclass
class StreamResult:
    tokens: list[str] = field(default_factory=list)
    token_times: list[float] = field(default_factory=list)
    final: dict[str, Any] | None = None
    errors: list[dict[str, Any]] = field(default_factory=list)
    elapsed: float = 0.0
    raw: str = ""


async def stream_chat(
    client: httpx.AsyncClient, query: str, conversation_id: str | None = None
) -> StreamResult:
    """Consume the SSE stream, timing each event as it arrives."""
    result = StreamResult()
    payload: dict[str, Any] = {"query": query}
    if conversation_id:
        payload["conversation_id"] = conversation_id

    started = time.perf_counter()
    event_name: str | None = None
    async with client.stream(
        "POST", f"{BASE}/api/v1/chat/stream", json=payload, timeout=300.0
    ) as response:
        response.raise_for_status()
        content_type = response.headers.get("content-type", "")
        if "text/event-stream" not in content_type:
            raise AssertionError(f"expected text/event-stream, got {content_type}")
        async for line in response.aiter_lines():
            if line.startswith("event:"):
                event_name = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                body = json.loads(line.split(":", 1)[1].strip())
                if event_name == "token":
                    result.tokens.append(body["text"])
                    result.token_times.append(time.perf_counter() - started)
                elif event_name == "done":
                    result.final = body
                elif event_name == "error":
                    result.errors.append(body)
    result.elapsed = time.perf_counter() - started
    return result


def verify_answerable(result: StreamResult) -> None:
    print("\n[1] answerable question: streaming + final shape")
    check(len(result.tokens) > 1, "streamed multiple token events", f"got {len(result.tokens)}")
    if len(result.token_times) > 1:
        spread = result.token_times[-1] - result.token_times[0]
        check(
            spread > 0.05,
            "tokens arrived incrementally (not one buffered burst)",
            f"spread {spread:.3f}s",
        )
    check(not result.errors, "no error events", str(result.errors))
    check(result.final is not None, "received a final event")
    if result.final is None:
        return

    check(
        set(result.final) == FINAL_KEYS,
        "final event has exactly the Section 6 keys",
        f"got {sorted(result.final)}",
    )
    check(
        result.final["has_sufficient_evidence"] is True,
        "has_sufficient_evidence is true",
        str(result.final["has_sufficient_evidence"]),
    )
    check(bool(result.final["answer"].strip()), "answer is non-empty")
    check(
        len(result.final["citations"]) >= 1,
        "at least one citation",
        f"got {len(result.final['citations'])}",
    )
    print(f"  answer: {result.final['answer'][:160]}")
    for citation in result.final["citations"]:
        check(
            set(citation) == CITATION_KEYS,
            f"citation {citation.get('id')} has exactly the documented keys",
            f"got {sorted(citation)}",
        )
        check(
            0.0 <= citation["relevance"] <= 1.0,
            f"citation {citation['id']} relevance in [0, 1]",
            str(citation["relevance"]),
        )
        check(bool(citation["matched_text"].strip()), f"citation {citation['id']} has matched_text")


def verify_off_topic(result: StreamResult) -> None:
    print("\n[2] off-topic question: gate refuses without the model")
    check(result.final is not None, "received a final event")
    if result.final is None:
        return
    check(
        result.final["has_sufficient_evidence"] is False,
        "has_sufficient_evidence is false",
        str(result.final["has_sufficient_evidence"]),
    )
    check(
        len(result.tokens) == 0,
        "no token events (the LLM was never called)",
        f"got {len(result.tokens)}",
    )
    check(
        result.final["citations"] == [],
        "no citations on a refusal",
        str(result.final["citations"]),
    )
    check(
        "couldn't find" in result.final["answer"].lower()
        or "could not find" in result.final["answer"].lower(),
        "answer explains the document was searched",
        result.final["answer"][:100],
    )
    print(f"  answer: {result.final['answer'][:160]}")


async def verify_excerpts(client: httpx.AsyncClient, citations: list[dict]) -> None:
    """Re-read each cited chunk from Qdrant and confirm the excerpt is verbatim."""
    print("\n[3] matched_text is a literal substring of the source chunk")
    if not citations:
        check(False, "have citations to verify")
        return

    from app.services.qdrant import get_qdrant_client

    qdrant = get_qdrant_client()
    collection = get_settings().qdrant_collection
    points, _ = await qdrant.scroll(collection_name=collection, limit=1000, with_payload=True)
    for citation in citations:
        # Find the chunk in the collection that contains the excerpt.
        source = None
        for point in points:
            payload = point.payload or {}
            if str(payload.get("document_id")) == str(citation["document_id"]):
                if citation["matched_text"] in payload.get("content", ""):
                    source = payload
                    break
        check(
            source is not None,
            f"citation {citation['id']} excerpt exists verbatim in a stored chunk",
            f"no chunk contains {citation['matched_text'][:60]!r}",
        )
        if source is not None:
            check(
                source.get("filename") == citation["document_name"],
                f"citation {citation['id']} document_name matches the stored chunk",
                f"{source.get('filename')} vs {citation['document_name']}",
            )
    del client


async def verify_persistence(client: httpx.AsyncClient, final: dict) -> None:
    """Query Postgres directly: the API returning data is not proof it stored it."""
    print("\n[4] conversations, messages and citations are really in Postgres")
    from sqlalchemy import func, select

    from app.db.models import Citation, Conversation, Message, User
    from app.db.session import session_scope

    answer_prefix = final["answer"][:40]

    async with session_scope() as session:
        user_count = await session.scalar(select(func.count(User.id)))
        conv_count = await session.scalar(select(func.count(Conversation.id)))
        msg_count = await session.scalar(select(func.count(Message.id)))
        cit_count = await session.scalar(select(func.count(Citation.id)))

        check(user_count == 1, "exactly one V1 user row", str(user_count))
        check(conv_count >= 2, "at least two conversations", str(conv_count))
        check(msg_count >= 4, "at least four messages (2 per conversation)", str(msg_count))
        check(cit_count >= 1, "at least one persisted citation", str(cit_count))

        stored = await session.scalar(
            select(Message).where(Message.content.like(f"{answer_prefix}%"))
        )
        check(stored is not None, "the streamed answer is stored verbatim")

        rows = (
            await session.execute(
                select(Citation.message_id, Citation.page_number, Citation.section, Citation.relevance_score, Citation.matched_text)
            )
        ).all()
        check(
            all(row[4] for row in rows),
            "every persisted citation has non-empty matched_text",
            f"{sum(1 for r in rows if not r[4])} row(s) with null",
        )
        check(
            all(0.0 <= row[3] <= 1.0 for row in rows),
            "every persisted relevance_score is in [0, 1]",
        )

        roles = (await session.execute(select(Message.role).distinct())).scalars().all()
        check(
            set(roles) == {"user", "assistant"},
            "both roles persisted",
            str(sorted(roles)),
        )


async def verify_conversation_endpoints(client: httpx.AsyncClient) -> None:
    print("\n[5] conversation list and detail endpoints")
    listed = (await client.get(f"{BASE}/api/v1/conversations")).json()
    check(listed["total"] >= 2, "list endpoint reports conversations", str(listed["total"]))
    check(
        all({"id", "title", "created_at", "message_count"} <= set(row) for row in listed["conversations"]),
        "summaries carry the documented fields",
    )

    # The most recent conversation is the off-topic refusal, which correctly has
    # no citations, so scan for the answer conversation rather than assuming it
    # is first.
    cited_conversation = None
    for summary in listed["conversations"]:
        detail_response = await client.get(f"{BASE}/api/v1/conversations/{summary['id']}")
        check(detail_response.status_code == 200, f"detail endpoint returns 200 for {summary['id'][:8]}")
        detail = detail_response.json()
        if any(message["citations"] for message in detail["messages"]):
            cited_conversation = detail
            break

    check(cited_conversation is not None, "some stored conversation has citations")
    if cited_conversation is not None:
        check(
            bool(cited_conversation["messages"]),
            "cited conversation has messages",
        )
        cited = [
            m for m in cited_conversation["messages"] if m["citations"]
        ][0]
        citation = cited["citations"][0]
        check(
            set(citation) == CITATION_KEYS,
            "replayed citation has the same shape as the live one",
            f"got {sorted(citation)}",
        )
        check(
            bool(citation["matched_text"]),
            "replayed citation still has matched_text",
            "history lost the excerpt",
        )
        check(
            0.0 <= citation["relevance"] <= 1.0,
            "replayed relevance survived the round trip",
            str(citation["relevance"]),
        )

    missing = await client.get(f"{BASE}/api/v1/conversations/{uuid.uuid4()}")
    check(missing.status_code == 404, "unknown conversation returns 404", str(missing.status_code))


async def main() -> int:
    print("=" * 70)
    print("Stage 6 verification against", BASE)
    print("=" * 70)

    async with httpx.AsyncClient() as client:
        health = await client.get(f"{BASE}/health", timeout=30.0)
        check(health.status_code == 200, "server is healthy")

        answerable = await stream_chat(client, ANSWERABLE)
        verify_answerable(answerable)
        if answerable.final and answerable.final["citations"]:
            await verify_excerpts(client, answerable.final["citations"])

        off_topic = await stream_chat(client, OFF_TOPIC)
        verify_off_topic(off_topic)

        if answerable.final:
            await verify_persistence(client, answerable.final)
        await verify_conversation_endpoints(client)

    print("\n" + "=" * 70)
    print(f"{checks - len(failures)}/{checks} checks passed")
    if failures:
        print("FAILED:")
        for failure in failures:
            print(f"  - {failure}")
    print("=" * 70)
    return 1 if failures else 0


if __name__ == "__main__":
    # This script queries Postgres directly, and psycopg's async driver cannot
    # use Windows' default ProactorEventLoop. See app/core/eventloop.py.
    configure_event_loop()
    sys.exit(asyncio.run(main()))
