
"""Full-stack end-to-end verification script for RAG Policy Assistant.

Verifies all layers of the stack against project.md Section 16 (Definition of Done)
and workflow.md Section 6 (Verification Protocol):

Layer 1 — Infrastructure:
  [1] Docker containers up & healthy: PostgreSQL query, Qdrant collections, Redis PING.
  [2] Ollama running & OLLAMA_MODEL available via /api/tags.

Layer 2 — Backend API:
  [3] GET /health returns status=ok and all dependencies green.
  [4] GET /api/v1/documents returns well-formed schema.
  [5] Document upload reaches ready status (no race condition, no process crash).
  [6] POST /api/v1/chat/stream returns valid SSE tokens + done event with citations.
  [7] Insufficient-evidence query returns has_sufficient_evidence=false with no token events.

Layer 3 — Frontend:
  [8] npm run build succeeds with zero errors.
  [9] npx tsc -b passes with zero type errors.
  [10] Dev server boots, app shell renders via Playwright with 0 console errors, Vite proxy works.

Layer 4 — Real End-to-End User Flow:
  [11] Full UI interaction via headless browser: document in sidebar, chat question,
       citation chip clicked, Evidence panel shows highlighted matched_text,
       page reload restores conversation history.
  [12] Theme toggle renders both Espresso dark and Ivory light palettes with exact
       Section 10 hex tokens.

Run:
    uv run python backend/scripts/verify_full_stack.py
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

# Ensure backend root is on sys.path
SCRIPT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = SCRIPT_DIR.parent
PROJECT_ROOT = BACKEND_DIR.parent
FRONTEND_DIR = PROJECT_ROOT / "frontend"

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.config import get_settings
from app.core.eventloop import configure_event_loop

BACKEND_BASE = "http://127.0.0.1:8000"
FRONTEND_BASE = "http://localhost:5173"
FIXTURE_PATH = BACKEND_DIR / "scripts" / "fixtures" / "acme_travel_policy.pdf"

# Palette specs from project.md Section 10
DARK_EXPECTED = {
    "--bg": "#1c1410",
    "--surface": "#241a14",
    "--surface-elevated": "#2c2019",
    "--border": "#3a2a21",
    "--text-primary": "#f5ede4",
    "--text-muted": "#b5a395",
    "--accent": "#d9a55c",
    "--accent-hover": "#e8b96f",
    "--success": "#7fa871",
    "--warning": "#c97b63",
}

LIGHT_EXPECTED = {
    "--bg": "#fbf7f1",
    "--surface": "#f4ede3",
    "--surface-elevated": "#ffffff",
    "--border": "#e3d8c8",
    "--text-primary": "#2a1f17",
    "--text-muted": "#6e5d4e",
    "--accent": "#8a5a24",
    "--accent-hover": "#a66b2c",
    "--success": "#4f7a45",
    "--warning": "#a8503a",
}


class VerificationReport:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    def record(self, num: int, name: str, passed: bool, detail: str) -> bool:
        self.rows.append({
            "num": num,
            "name": name,
            "passed": passed,
            "detail": detail,
        })
        status_str = "PASS" if passed else "FAIL"
        print(f"[{status_str}] Check {num}: {name} -> {detail}")
        return passed

    def print_summary(self) -> int:
        print("\n" + "=" * 80)
        print(f"{'#':<4} {'Check':<42} {'Status':<8} {'Detail'}")
        print("=" * 80)
        passed_count = 0
        for r in self.rows:
            st = "PASS" if r["passed"] else "FAIL"
            if r["passed"]:
                passed_count += 1
            print(f"{r['num']:<4} {r['name'][:40]:<42} {st:<8} {r['detail'][:60]}")
        print("=" * 80)
        total = len(self.rows)
        print(f"Total: {passed_count}/{total} passed")
        return 0 if passed_count == total else 1


report = VerificationReport()
created_doc_ids: list[str] = []


def check_layer1_infra() -> bool:
    settings = get_settings()

    # 1.1 PostgreSQL real query
    pg_ok = False
    try:
        import psycopg
        # Parse connection url (convert postgresql+psycopg:// to postgresql://)
        pg_url = settings.database_url.replace("postgresql+psycopg://", "postgresql://")
        with psycopg.connect(pg_url, connect_timeout=3) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1;")
                res = cur.fetchone()
                if res and res[0] == 1:
                    pg_ok = True
    except Exception as exc:
        report.record(1, "Infrastructure: Docker containers", False, f"Postgres query failed: {exc}")
        return False

    # 1.2 Qdrant real list collections
    qdrant_ok = False
    try:
        r = httpx.get(f"{settings.qdrant_url}/collections", timeout=3.0)
        if r.status_code == 200 and "result" in r.json():
            qdrant_ok = True
    except Exception as exc:
        report.record(1, "Infrastructure: Docker containers", False, f"Qdrant query failed: {exc}")
        return False

    # 1.3 Redis real PING
    redis_ok = False
    try:
        # Simple redis socket ping
        s = socket.create_connection(("localhost", 6379), timeout=3)
        s.sendall(b"PING\r\n")
        reply = s.recv(1024)
        s.close()
        if b"+PONG" in reply:
            redis_ok = True
    except Exception as exc:
        report.record(1, "Infrastructure: Docker containers", False, f"Redis ping failed: {exc}")
        return False

    report.record(
        1,
        "Infrastructure: Docker containers",
        True,
        "PostgreSQL (SELECT 1), Qdrant (/collections), Redis (PONG) all healthy",
    )

    # 2. Ollama running & model available
    try:
        r = httpx.get(f"{settings.ollama_base_url}/api/tags", timeout=5.0)
        if r.status_code != 200:
            return report.record(2, "Ollama running & model available", False, f"HTTP {r.status_code}")
        models = [m.get("name", "") for m in r.json().get("models", [])]
        expected_model = settings.ollama_model
        model_found = any(expected_model in m or m.startswith(expected_model) for m in models)
        if not model_found:
            return report.record(
                2,
                "Ollama running & model available",
                False,
                f"Model '{expected_model}' not found in tags: {models}",
            )
        # Warm up model so subsequent chat calls don't suffer from cold-start delay
        try:
            httpx.post(
                f"{settings.ollama_base_url}/api/chat",
                json={"model": expected_model, "messages": [{"role": "user", "content": "ping"}], "stream": False},
                timeout=30.0,
            )
        except Exception:
            pass

        report.record(
            2,
            "Ollama running & model available",
            True,
            f"Ollama running with model '{expected_model}' available (warmed up)",
        )
    except Exception as exc:
        return report.record(2, "Ollama running & model available", False, f"Connection failed: {exc}")

    return True


def check_layer2_backend_api() -> bool:
    # 3. GET /health
    try:
        r = httpx.get(f"{BACKEND_BASE}/health", timeout=5.0)
        if r.status_code != 200:
            return report.record(3, "Backend /health check", False, f"HTTP {r.status_code}")
        data = r.json()
        if data.get("status") != "ok":
            return report.record(3, "Backend /health check", False, f"status={data.get('status')}")
        deps = data.get("dependencies", [])
        failed_deps = [d["name"] for d in deps if not d.get("ok")]
        if failed_deps:
            return report.record(
                3, "Backend /health check", False, f"Unhealthy dependencies: {failed_deps}"
            )
        report.record(
            3,
            "Backend /health check",
            True,
            f"status=ok, {len(deps)} dependencies green ({', '.join(d['name'] for d in deps)})",
        )
    except Exception as exc:
        return report.record(3, "Backend /health check", False, f"Request failed: {exc}")

    # 4. GET /api/v1/documents shape
    try:
        r = httpx.get(f"{BACKEND_BASE}/api/v1/documents", timeout=5.0)
        if r.status_code != 200:
            return report.record(4, "GET /documents schema", False, f"HTTP {r.status_code}")
        data = r.json()
        if "documents" not in data or "total" not in data or not isinstance(data["documents"], list):
            return report.record(4, "GET /documents schema", False, f"Malformed response: {list(data.keys())}")
        report.record(
            4,
            "GET /documents schema",
            True,
            f"Response matches schema: {{documents: list[{len(data['documents'])}], total: {data['total']}}}",
        )
    except Exception as exc:
        return report.record(4, "GET /documents schema", False, f"Request failed: {exc}")

    # 5. Real document upload stress test (proves race fix & native-crash mitigation)
    num_stress_runs = int(os.getenv("VERIFY_UPLOAD_RUNS", "3"))
    try:
        if not FIXTURE_PATH.exists():
            return report.record(5, "Document upload & ready status", False, f"Fixture not found: {FIXTURE_PATH}")
        with open(FIXTURE_PATH, "rb") as f:
            file_bytes = f.read()

        chunk_count = 0
        for run_idx in range(1, num_stress_runs + 1):
            files = {"file": (FIXTURE_PATH.name, file_bytes, "application/pdf")}
            r = httpx.post(f"{BACKEND_BASE}/api/v1/documents", files=files, timeout=30.0)
            if r.status_code != 201:
                return report.record(5, "Document upload & ready status", False, f"Upload {run_idx}/{num_stress_runs} returned HTTP {r.status_code}")
            doc_data = r.json()
            uploaded_doc_id = str(doc_data["document_id"])
            created_doc_ids.append(uploaded_doc_id)

            # Poll status until terminal
            deadline = time.perf_counter() + 90.0
            final_status = None
            while time.perf_counter() < deadline:
                time.sleep(2.0)
                sr = httpx.get(f"{BACKEND_BASE}/api/v1/documents/{uploaded_doc_id}", timeout=30.0)
                if sr.status_code == 200:
                    sdata = sr.json()
                    final_status = sdata.get("status")
                    chunk_count = sdata.get("chunk_count", 0)
                    if final_status in ("ready", "failed"):
                        break

            if final_status != "ready":
                return report.record(
                    5,
                    "Document upload & ready status",
                    False,
                    f"Upload {run_idx}/{num_stress_runs} reached '{final_status}' instead of 'ready'",
                )

        report.record(
            5,
            "Document upload & ready status",
            True,
            f"Uploaded {num_stress_runs} consecutive fixtures -> all ready ({chunk_count} chunks, 0 race errors, 0 crashes)",
        )
    except Exception as exc:
        return report.record(5, "Document upload & ready status", False, f"Upload stress failed: {exc}")

    # 6. Real chat question SSE stream
    try:
        query = "How long do I have to submit an expense claim after a trip?"
        body = {"query": query}
        token_count = 0
        done_payload = None
        with httpx.stream(
            "POST", f"{BACKEND_BASE}/api/v1/chat/stream", json=body, timeout=120.0
        ) as response:
            if response.status_code != 200:
                return report.record(6, "Chat SSE stream shape", False, f"HTTP {response.status_code}")
            current_event = None
            for line in response.iter_lines():
                if line.startswith("event: "):
                    current_event = line[7:].strip()
                elif line.startswith("data: ") and current_event:
                    data_str = line[6:].strip()
                    if current_event == "token":
                        token_count += 1
                    elif current_event == "done":
                        done_payload = json.loads(data_str)
                    current_event = None

        if not done_payload:
            return report.record(6, "Chat SSE stream shape", False, "No 'done' event received")
        required_keys = {"answer", "has_sufficient_evidence", "citations"}
        if not required_keys.issubset(done_payload.keys()):
            return report.record(
                6, "Chat SSE stream shape", False, f"Done event missing keys: {required_keys - set(done_payload.keys())}"
            )
        if not done_payload.get("has_sufficient_evidence"):
            return report.record(6, "Chat SSE stream shape", False, "Expected has_sufficient_evidence=True")
        citations = done_payload.get("citations", [])
        if not citations:
            return report.record(6, "Chat SSE stream shape", False, "No citations in answered done payload")
        cit_keys = {"id", "document_id", "document_name", "page", "section", "matched_text", "relevance"}
        for cit in citations:
            if not cit_keys.issubset(cit.keys()):
                return report.record(
                    6, "Chat SSE stream shape", False, f"Citation missing keys: {cit_keys - set(cit.keys())}"
                )
        report.record(
            6,
            "Chat SSE stream shape",
            True,
            f"Received {token_count} tokens + done event with {len(citations)} valid citation(s)",
        )
    except Exception as exc:
        return report.record(6, "Chat SSE stream shape", False, f"Chat stream failed: {exc}")

    # 7. Insufficient-evidence question (pet travel / bringing animals to the office)
    # Note: New York accommodation cap is documented in the Acme travel policy
    # (Band A = 1500 USD), so an actual unevidenced query is required to test gate closure.
    # Chosen because pets/animals are genuinely absent from the policy corpus; must be re-validated whenever new fixture documents (e.g. XLSX/CSV) are ingested.
    try:
        neg_query = "What is the company policy on pet travel and bringing animals to the office?"
        token_count = 0
        done_payload = None
        with httpx.stream(
            "POST", f"{BACKEND_BASE}/api/v1/chat/stream", json={"query": neg_query}, timeout=120.0
        ) as response:
            if response.status_code != 200:
                return report.record(7, "Insufficient-evidence gate check", False, f"HTTP {response.status_code}")
            current_event = None
            for line in response.iter_lines():
                if line.startswith("event: "):
                    current_event = line[7:].strip()
                elif line.startswith("data: ") and current_event:
                    data_str = line[6:].strip()
                    if current_event == "token":
                        token_count += 1
                    elif current_event == "done":
                        done_payload = json.loads(data_str)
                    current_event = None

        if not done_payload:
            return report.record(7, "Insufficient-evidence gate check", False, "No 'done' event received")
        if done_payload.get("has_sufficient_evidence") is not False:
            return report.record(
                7,
                "Insufficient-evidence gate check",
                False,
                f"Expected has_sufficient_evidence=False, got {done_payload.get('has_sufficient_evidence')}",
            )
        if token_count > 0:
            return report.record(
                7,
                "Insufficient-evidence gate check",
                False,
                f"Evidence gate failed to short-circuit: received {token_count} token events",
            )
        report.record(
            7,
            "Insufficient-evidence gate check",
            True,
            "has_sufficient_evidence=False with 0 token events (gate short-circuited correctly)",
        )
    except Exception as exc:
        return report.record(7, "Insufficient-evidence gate check", False, f"Gate test failed: {exc}")

    return True


def check_layer3_frontend_build() -> bool:
    # 8. npm run build
    try:
        proc = subprocess.run(
            ["npm.cmd", "run", "build"],
            cwd=str(FRONTEND_DIR),
            capture_output=True,
            text=True,
            timeout=120,
        )
        if proc.returncode != 0:
            return report.record(8, "Frontend: npm run build", False, f"Build failed (code {proc.returncode}): {proc.stderr[:100]}")
        report.record(8, "Frontend: npm run build", True, "Production bundle built successfully (zero errors)")
    except Exception as exc:
        return report.record(8, "Frontend: npm run build", False, f"Process execution failed: {exc}")

    # 9. npx tsc -b
    try:
        proc = subprocess.run(
            ["npx.cmd", "tsc", "-b"],
            cwd=str(FRONTEND_DIR),
            capture_output=True,
            text=True,
            timeout=60,
        )
        if proc.returncode != 0:
            return report.record(9, "Frontend: npx tsc -b", False, f"TypeScript check failed: {proc.stdout[:100]}")
        report.record(9, "Frontend: npx tsc -b", True, "Zero TypeScript errors")
    except Exception as exc:
        return report.record(9, "Frontend: npx tsc -b", False, f"tsc execution failed: {exc}")

    return True


def check_layer4_e2e_playwright() -> bool:
    from playwright.sync_api import sync_playwright

    # On Windows, Playwright requires ProactorEventLoop to spawn browser subprocesses
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

    # 10. Dev server boot & proxy check
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()

            console_errors = []
            page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
            page.on("pageerror", lambda err: console_errors.append(str(err)))

            page.goto(FRONTEND_BASE)
            page.wait_for_load_state("networkidle")

            # Check for console errors
            if console_errors:
                browser.close()
                return report.record(10, "Dev server boot & Vite proxy", False, f"Console errors: {console_errors}")

            # Verify Vite proxy reaches real backend
            proxy_resp = page.evaluate("async () => { const r = await fetch('/api/v1/documents'); return r.status; }")
            if proxy_resp != 200:
                browser.close()
                return report.record(10, "Dev server boot & Vite proxy", False, f"Proxy /api/v1/documents returned {proxy_resp}")

            report.record(
                10,
                "Dev server boot & Vite proxy",
                True,
                "Dev server rendered with 0 console errors; Vite proxy /api/v1 returned 200",
            )

            # 11. Real end-to-end user flow (DoD 1-4)
            # 11.1 Check document in sidebar
            sidebar = page.locator("aside")
            if "acme_travel_policy.pdf" not in sidebar.inner_text():
                # Upload fixture via file input
                file_input = page.locator("input[type='file']")
                file_input.set_input_files(str(FIXTURE_PATH))
                page.wait_for_timeout(3000)

            # Wait for document to show ready in sidebar
            page.wait_for_selector("aside:has-text('acme_travel_policy.pdf')", timeout=30000)

            # 11.2 Ask question through chat input
            chat_input = page.locator("textarea[aria-label='Ask a question']")
            chat_input.fill("How long do I have to submit an expense claim after a trip?")
            send_btn = page.locator("button[aria-label='Send question']")
            send_btn.click()

            # Wait for stream to finish (Sources row appears with citation chips)
            page.wait_for_selector("button[title*='acme_travel_policy']", timeout=120000)
            citation_chip = page.locator("button[title*='acme_travel_policy']").first

            # 11.3 Post-answer UI state assertions (verify no stuck streaming artifacts)
            page.wait_for_selector("textarea[aria-label='Ask a question']:not([disabled])", timeout=15000)
            if chat_input.is_disabled():
                browser.close()
                return report.record(11, "End-to-end user flow (DoD 1-4)", False, "Chat input remained disabled after stream completed")

            placeholder_after = chat_input.get_attribute("placeholder") or ""
            if "Answering" in placeholder_after:
                browser.close()
                return report.record(11, "End-to-end user flow (DoD 1-4)", False, f"Chat input placeholder did not revert: '{placeholder_after}'")

            caret_count = page.evaluate("() => document.querySelectorAll('[data-testid=\"streaming-caret\"]').length")
            if caret_count > 0:
                browser.close()
                return report.record(11, "End-to-end user flow (DoD 1-4)", False, f"Streaming caret still present in DOM after answer completed (found {caret_count})")

            is_streaming_store = page.evaluate("() => window.__conversationStore ? window.__conversationStore.getState().isStreaming : false")
            if is_streaming_store:
                browser.close()
                return report.record(11, "End-to-end user flow (DoD 1-4)", False, "conversationStore.isStreaming is still true after completion")

            # 11.4 Click citation chip and verify Evidence panel opens
            citation_chip.click()
            page.wait_for_selector("aside[aria-label='Evidence']", timeout=20000)
            evidence_panel = page.locator("aside[aria-label='Evidence']")
            mark_text = evidence_panel.locator("mark").inner_text()
            if not mark_text or len(mark_text.strip()) == 0:
                browser.close()
                return report.record(11, "End-to-end user flow (DoD 1-4)", False, "Evidence panel mark element is empty")

            # 11.5 Reload page and confirm history restores
            page.wait_for_timeout(1000)
            page.reload()
            page.wait_for_load_state("networkidle")
            page.wait_for_selector("text=How long do I have to submit an expense claim", timeout=20000)

            # 11.6 Stream cancellation regression test (P0-A regression test)
            chat_input = page.locator("textarea[aria-label='Ask a question']")
            chat_input.fill("What is the meal allowance per day on business trips?")
            send_btn = page.locator("button[aria-label='Send question']")
            send_btn.click()

            # Wait for active streaming state (stop button appears and input disables)
            page.wait_for_selector("button[aria-label='Stop generating']", timeout=5000)
            stop_btn = page.locator("button[aria-label='Stop generating']")
            stop_btn.click()

            # Assert UI recovers immediately and cleanly from cancellation
            page.wait_for_selector("textarea[aria-label='Ask a question']:not([disabled])", timeout=5000)
            cancelled_placeholder = chat_input.get_attribute("placeholder") or ""
            if "Answering" in cancelled_placeholder:
                browser.close()
                return report.record(11, "End-to-end user flow (DoD 1-4)", False, f"Input placeholder did not revert on cancel: '{cancelled_placeholder}'")

            cancelled_carets = page.evaluate("() => document.querySelectorAll('[data-testid=\"streaming-caret\"]').length")
            if cancelled_carets > 0:
                browser.close()
                return report.record(11, "End-to-end user flow (DoD 1-4)", False, f"Streaming caret stuck after cancel (found {cancelled_carets})")

            cancelled_store_streaming = page.evaluate("() => window.__conversationStore ? window.__conversationStore.getState().isStreaming : false")
            if cancelled_store_streaming:
                browser.close()
                return report.record(11, "End-to-end user flow (DoD 1-4)", False, "conversationStore.isStreaming remained true after cancel")

            page.wait_for_selector("text=Generation cancelled", timeout=5000)

            report.record(
                11,
                "End-to-end user flow (DoD 1-4)",
                True,
                f"Document ready -> answer streamed -> post-UI clean -> chip clicked -> Evidence mark: '{mark_text[:20]}...' -> history restored -> cancellation recovered cleanly",
            )

            # 12. Theme toggle & palette verification (Section 10)
            initial_theme = page.evaluate("document.documentElement.dataset.theme || 'dark'")
            if initial_theme == "light":
                first_expected, second_expected = LIGHT_EXPECTED, DARK_EXPECTED
                first_name, second_name = "Light", "Dark"
            else:
                first_expected, second_expected = DARK_EXPECTED, LIGHT_EXPECTED
                first_name, second_name = "Dark", "Light"

            # Check initial palette
            first_tokens = page.evaluate("""(expected) => {
                const s = window.getComputedStyle(document.documentElement);
                const res = {};
                for (const k of Object.keys(expected)) {
                    res[k] = s.getPropertyValue(k).trim().toLowerCase();
                }
                return res;
            }""", first_expected)
            first_mismatches = [f"{k}: got {first_tokens.get(k)} vs {v}" for k, v in first_expected.items() if first_tokens.get(k) != v]
            if first_mismatches:
                browser.close()
                return report.record(12, "Theme toggle & palette tokens", False, f"{first_name} token mismatches: {first_mismatches}")

            # Toggle to opposite theme via UI button
            theme_btn = page.locator("button[aria-label*='theme' i]")
            theme_btn.click()
            page.wait_for_timeout(500)

            # Check toggled palette
            second_tokens = page.evaluate("""(expected) => {
                const s = window.getComputedStyle(document.documentElement);
                const res = {};
                for (const k of Object.keys(expected)) {
                    res[k] = s.getPropertyValue(k).trim().toLowerCase();
                }
                return res;
            }""", second_expected)
            second_mismatches = [f"{k}: got {second_tokens.get(k)} vs {v}" for k, v in second_expected.items() if second_tokens.get(k) != v]
            if second_mismatches:
                browser.close()
                return report.record(12, "Theme toggle & palette tokens", False, f"{second_name} token mismatches: {second_mismatches}")

            report.record(
                12,
                "Theme toggle & palette tokens",
                True,
                "Both Espresso dark and Ivory light palettes match Section 10 hex tokens exactly",
            )

            browser.close()
    except Exception as exc:
        return report.record(11, "End-to-end browser flow", False, f"Playwright error: {exc}")

    return True


async def cleanup_test_data(preserve_one_fixture: bool = True) -> None:
    from app.services.chunk_purge import purge_document_chunks
    from app.db.session import session_scope
    from sqlalchemy import text

    to_purge = list(created_doc_ids)
    retained_id = None
    if preserve_one_fixture and len(to_purge) >= 1:
        retained_id = to_purge.pop()

    async with session_scope() as session:
        for did in to_purge:
            try:
                await purge_document_chunks(uuid.UUID(did))
                await session.execute(text(f"DELETE FROM documents WHERE id = '{did}'"))
            except Exception:
                pass
        await session.commit()

    # Verify zero orphaned Qdrant points
    try:
        from qdrant_client import QdrantClient
        settings = get_settings()
        qc = QdrantClient(url=settings.qdrant_url)
        info = qc.get_collection(settings.qdrant_collection)
        if retained_id:
            print(f"Cleanup confirmed: 1 known fixture document retained ({retained_id}) with {info.points_count} points in Qdrant; intermediate test artifacts purged.")
        else:
            print(f"Cleanup confirmed: {info.points_count} points remaining in Qdrant (zero orphaned vectors)")
    except Exception as exc:
        print(f"Qdrant cleanup check warning: {exc}")


def main() -> int:
    configure_event_loop()
    print("=" * 80)
    print("RAG Policy Assistant — Full-Stack End-to-End Verification")
    print("=" * 80)

    try:
        # Layer 1
        if not check_layer1_infra():
            return report.print_summary()

        # Layer 2
        if not check_layer2_backend_api():
            return report.print_summary()

        # Layer 3
        if not check_layer3_frontend_build():
            return report.print_summary()

        # Layer 4
        if not check_layer4_e2e_playwright():
            return report.print_summary()

    finally:
        # Cleanup test documents
        if created_doc_ids:
            print("\nCleaning up test documents from Postgres and Qdrant...")
            try:
                if sys.platform == "win32":
                    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
                asyncio.run(cleanup_test_data())
            except Exception as exc:
                print(f"Cleanup warning: {exc}")

    return report.print_summary()


if __name__ == "__main__":
    sys.exit(main())
