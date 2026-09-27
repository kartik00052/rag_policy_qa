"""Prove the metadata assertions in verify_stage3 are not no-ops.

Calls ``verify_metadata_keys`` with deliberately broken payloads and asserts it
reports each defect. A verification script that cannot fail is worthless, so this
guards against the exact regression being repaired here: a check that prints PASS
because its condition was hardcoded.
"""

from __future__ import annotations

import asyncio
import sys

from scripts.verify_stage3 import REQUIRED_PAYLOAD_KEYS, failures, verify_metadata_keys

GOOD = {
    "document_id": "11111111-1111-1111-1111-111111111111",
    "chunk_index": 0,
    "page_number": 1,
    "section": "1. Intro",
    "heading_path": ["1. Intro"],
    "content": "some text",
    "content_type": "text",
}


def run_case(name: str, records: list[dict], expect_failure: bool) -> bool:
    failures.clear()
    verify_metadata_keys(records)
    failed = bool(failures)
    ok = failed == expect_failure
    print(
        f"  [{'PASS' if ok else 'FAIL'}] {name} -> "
        f"{'detected' if failed else 'not detected'} "
        f"({len(failures)} failure(s) recorded)"
    )
    for line in failures:
        print(f"           {line}")
    return ok


async def main() -> int:
    print("=== negative tests for verify_metadata_keys ===")
    results = [
        run_case("well-formed payload passes", [dict(GOOD)], expect_failure=False),
        run_case(
            "missing 'content' is caught",
            [{k: v for k, v in GOOD.items() if k != "content"}],
            expect_failure=True,
        ),
        run_case(
            "missing 'content_type' is caught",
            [{k: v for k, v in GOOD.items() if k != "content_type"}],
            expect_failure=True,
        ),
        run_case(
            "empty content is caught",
            [{**GOOD, "content": ""}],
            expect_failure=True,
        ),
        run_case(
            "several bad payloads are ALL reported, not just the first",
            [
                {k: v for k, v in GOOD.items() if k != "content"},
                {k: v for k, v in GOOD.items() if k != "section"},
                {k: v for k, v in GOOD.items() if k != "heading_path"},
            ],
            expect_failure=True,
        ),
    ]
    print(f"\nrequired keys asserted: {list(REQUIRED_PAYLOAD_KEYS)}")
    passed = sum(results)
    print(f"{passed}/{len(results)} negative tests behaved as expected")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
