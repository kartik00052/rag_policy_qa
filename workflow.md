# Agent Workflow — RAG Policy Assistant

> This file governs **how** work gets done. `project.md` governs **what** gets built
> (scope, stack, schema, UI). Read `project.md` first, in full, before touching any
> code. If something you're about to do isn't backed by a decision in `project.md`,
> stop and treat that as missing information — don't invent it.
>
> **Status: Stages 0–9 are complete** (project.md Section 18's signed-off Definition of
> Done). **Stage 10 (below) is the current, active priority** — the LLM provider
> migration from local Ollama to pluggable Groq/Gemini/Ollama. Start there, not at
> Stage 0, unless you're specifically asked to revisit earlier-stage work.

---

## 1. Golden Rules (never break these)

1. **`project.md` is the only source of truth for scope, stack, schema, endpoints, and
   UI.** Never introduce a library, service, table, endpoint, or design choice that
   isn't in it. If a task seems to need something outside it, say so explicitly and
   propose adding it to `project.md` first — don't just add it in code.
   *(Exception already granted and documented: `langchain-groq` and
   `langchain-google-genai` are pre-authorized as of project.md Section 4.1 — adding
   them is not a Rule 1 violation, it's already-scoped work.)*
2. **Never build anything under a `(V2)` heading in `project.md`.** Not a stub route,
   not a placeholder table column, not a disabled nav item that references a page that
   doesn't exist yet. If asked to "start on the admin dashboard" or "add comparison,"
   check `project.md` Section 2's out-of-scope list and say it's V2 before doing anything.
3. **Follow the Build Order in Section 3 of this file in sequence.** Stages 0–9 are
   done; don't re-litigate them without a specific reason. Stage 10 (LLM provider
   migration) has its own internal sequence — don't skip its recalibration step to get
   to "it's fast now" faster; a fast, unverified answer is not an improvement over a
   slow, verified one for this project.
4. **Never guess a library's API.** If you're not certain of a function signature,
   parameter name, model name, or return shape for Qdrant, LangGraph, Docling,
   SQLAlchemy 2.0 async, shadcn/Base UI, or — newly relevant — `langchain-groq` /
   `langchain-google-genai` / their actual current model names and rate limits, check
   the installed package's actual source/docs or current official documentation before
   writing code that calls it. Do not pattern-match from a different version's API, or
   from stale training-data knowledge of what models/limits were once available, and
   assume it still applies — provider-hosted model lineups and limits change frequently.
5. **Run and verify before declaring something done.** "This should work" is not a
   completion state, and neither is "the automated check passed" without having also
   sanity-checked that the check is testing the right thing. This project has a real,
   documented history of verification scripts reporting "all green" while a genuine,
   visible bug was live in the app (the stuck-streaming-state incident) — a passing
   script is evidence, not proof, until you've also reasoned about what it could be
   missing.
6. **Never commit `.venv/`, `node_modules/`, `.env`, or any file over ~10MB.** Check
   `.gitignore` exists and is correct *before* the first commit in any fresh clone or
   fresh venv setup.
7. **Don't touch unrelated files.** A task about the LLM provider doesn't get to also
   "clean up" the ingestion module unless that's part of the task. Small, scoped diffs,
   one commit per concern.
8. **When something in `project.md` conflicts with a request in conversation, ask.**
   Don't silently pick one. Say plainly: "project.md specifies X for this, you asked
   for Y — which should I follow?"
9. **No secrets, API keys, or credentials in code, commits, or logs.** `GROQ_API_KEY`
   and `GOOGLE_API_KEY` follow the same rule as every other secret: `.env` only, never
   committed, `.env.example` gets a placeholder, never a real value.
10. **Don't add error-swallowing (`except: pass`, empty `catch` blocks, silent fallbacks
    that hide failures).** This includes a frontend `catch` branch that returns early
    without resetting state — the project's worst UI bug to date came from exactly this
    pattern (a cancelled-stream `return;` that never reset `isStreaming`). Any new
    cancellation/error path in LLM provider code must reset state fully, not just avoid
    throwing.
11. **Re-prove safety/quality mechanisms for every new LLM provider — never assume they
    transfer.** A verbatim-restatement check, a contradiction detector, or a
    prompt-injection safeguard that was calibrated against one model's specific failure
    tendencies is not automatically meaningful against a different model. "The check
    still passes" and "the check is still catching something real" are different claims
    — confirm both, per Stage 10 below.

---

## 2. Before Writing Any Code — Checklist

Run through this at the start of every work session, not just the first one:

- [ ] Re-read the relevant section(s) of `project.md` for this task — don't rely on
      memory of it from earlier in the conversation if it's been a while.
- [ ] Confirm which Build Order stage (Section 3 below) this task belongs to. If it's
      Stage 10 (LLM provider work), confirm which of its sub-steps you're on — don't
      restart recalibration work that a prior session already completed and evidenced.
- [ ] Confirm the task doesn't touch anything under a `(V2)` heading.
- [ ] Check `git status` — working tree should be clean or only contain the previous
      task's intended changes before starting a new one.
- [ ] Confirm the venv/uv environment is active (`uv sync` if unsure) and, separately,
      confirm the Docker stack cold-starts cleanly if you haven't checked this
      session — `docker-compose.yml`'s Postgres port is pinned to `5434:5432`
      permanently now; if a fresh `docker compose up -d` doesn't connect cleanly on the
      first try, treat that as a regression worth investigating, not a known quirk to
      route around again.
- [ ] If frontend: confirm `npm install` is up to date if `package.json` changed since
      last session.
- [ ] If about to trust a verification script's "PASS" output: briefly consider what
      that specific check does and does not assert (positive presence vs. absence of
      unwanted state) before treating the result as proof of health — per Golden Rule 5.

---

## 3. Build Order — follow this sequence, stage by stage

Stages 0–9 are complete and signed off (project.md Section 18). They remain here as the
historical record and as the checkpoints to re-run if a regression is ever suspected in
that layer. **Stage 10 is the current, active stage.**

### Stage 0 — Skeleton ✅ DONE
FastAPI app boots, `/health` responds. Postgres/Qdrant/Redis via `docker-compose up`.
React app boots via Vite. **Checkpoint met.**

### Stage 1 — Data layer ✅ DONE
SQLAlchemy models for the V1 schema + Alembic migration, live-verified against the
running database (`project.md` Section 5 matches the actual schema exactly, including
`citations.matched_text`). **Checkpoint met.**

### Stage 2 — Document ingestion ✅ DONE
All four formats (PDF, DOCX, XLSX, CSV) verified end-to-end: upload → Docling parse →
chunk → embed → Qdrant + Postgres → `status=ready`, with real chunk/metadata inspection,
not just "no error thrown." **Checkpoint met.**

### Stage 3 — Retrieval ✅ DONE
Dense + sparse + RRF fusion, verified against known questions with the correct chunk
actually present in fused top-N. **Checkpoint met.**

### Stage 4 — Reranking + evidence gating ✅ DONE
Cross-encoder rerank, `has_sufficient_evidence` gating, calibrated and documented
threshold, known false-negative/false-positive cases recorded rather than silently
tuned away. **Checkpoint met.**

### Stage 5 — LangGraph + LLM generation ✅ DONE (Ollama baseline)
Graph built exactly per `project.md` Section 4's `RAGState`/node sequence, citations
with `matched_text`, prompt-injection safeguard verified. **Checkpoint met** — against
Ollama. Re-verification against new providers is Stage 10's job, not a reopening of
this stage.

### Stage 6 — Streaming + chat API ✅ DONE
SSE streaming, conversation/message/citation persistence, verified live with real
token-by-token delivery and the correct final event shape. **Checkpoint met.**

### Stage 7 — Frontend: functional shell ✅ DONE
Three-pane shell, all four Zustand stores, real streaming chat wired to the real
backend, clickable citation chips, Evidence panel with real `matched_text` highlighting.
**Checkpoint met** — `project.md` Section 16 items 1–4 hold against the real backend.

### Stage 8 — Visual identity pass ✅ DONE
Espresso/Ivory color system, Fraunces/Geist typography, every documented component
state implemented and verified, theme switch with zero layout shift.
**Checkpoint met** — Definition of Done items 5–6.

### Stage 9 — Hardening pass ✅ DONE
Real error states (upload failure, insufficient evidence, cancelled generation),
Docker cold-start reliability fixed (Postgres port pinned, MinIO removed), the
flush-vs-commit ingestion race fixed with a load-tested regression suite, the
native Docling access-violation crash diagnosed (blast radius: kills the whole
process) and mitigated (fresh `DocumentConverter` per conversion), the
permanently-stuck-streaming-UI bug root-caused and fixed with a reset-state
`cancelStream` action. **Checkpoint met** — see `project.md` Section 18 for the full,
dated scorecard and the honest audit trail behind it.

### Stage 10 — LLM Provider Migration (CURRENT PRIORITY)

**Goal:** move the default generation provider from local Ollama (correct but ~30–60s/
answer on this CPU-only machine) to Groq (fast, cloud) while keeping Ollama as a real,
working fallback and keeping Gemini as a secondary cloud option. This is V1.1 scope per
`project.md` Section 2 — not a new product feature, a backend swap.

**Sub-steps, in order — do not skip the recalibration step to ship faster:**

1. **Design the abstraction.** A provider-agnostic `LLMProvider` interface (streaming
   `generate()`) that `graph.py` calls without knowing which backend is active. Three
   implementations via LangChain (`langchain-ollama`, `langchain-groq`,
   `langchain-google-genai`), selected by `LLM_PROVIDER` in `.env`
   (`project.md` Section 15). Look up current model names and rate limits from each
   provider's actual documentation — don't hardcode from memory (Golden Rule 4).
2. **Handle real behavioral differences.** Streaming chunk granularity, rate-limit
   surfacing (visible error, not silent failure — Golden Rule 10), and confirming the
   "retrieved text is untrusted data" system-prompt instruction actually transfers into
   each new provider's message format.
3. **Re-calibrate safety/quality checks per provider (the step most likely to get
   skipped under time pressure — don't skip it):**
   - Re-run `verify_injection.py` against the new provider, multiple runs, not one.
   - Re-run `verify_generation.py --runs 3` and report the real pass/fail table.
   - Specifically re-test the project's two historically hardest questions (the
     business-class contradiction question, the meal-allowance citation question).
   - If the new provider's failure modes differ from qwen2.5:3b's (e.g. different
     restatement behavior, different citation-attribution tendencies), extend the
     checks to actually catch the new failure mode — don't report false confidence
     because the old checks happen to still pass for an unrelated reason.
4. **Measure the actual latency improvement** — real wall-clock numbers for at least 5
   real questions, Ollama vs. the new provider, for this project's actual prompt sizes.
   Don't assume the improvement; prove it.
5. **Update `verify_full_stack.py`** to exercise whichever provider is configured
   rather than hardcoding Ollama-specific assumptions, and add it as a permanent,
   re-runnable check (per this project's established pattern of folding every ad hoc
   verification into the permanent suite rather than leaving it as a one-off).
6. **Update `project.md`** — Section 4.1, Section 15's env vars, and Section 18's
   boundary note — once a provider is genuinely verified, not before.

**Checkpoint:** `project.md` Section 16's Definition of Done items 2 and 4 re-verified
and passing against the new default provider, with real evidence (not inspection), and
Ollama confirmed still functional as a fallback. Only after this checkpoint is met
should the new provider be treated as the real default in any demo or further work.

**Only after Stage 10's checkpoint is met** should `project.md` Section 13 (Comparison)
or Section 14 (Admin Dashboard) be started, and only when explicitly requested.

---

## 4. Coding Conventions — Backend

- Async all the way through (`async def` routes, async SQLAlchemy session, async Qdrant
  client, async LangChain provider calls) — don't mix sync calls into the async request
  path.
- One responsibility per module: a route handler calls a service function; the service
  function calls the RAG graph / repository layer. Don't put business logic directly in
  route handlers.
- Every Pydantic model used in a request/response gets explicit field types — no bare
  `dict` payloads for anything the frontend depends on. (This project has already hit a
  real bug from an overly-strict Pydantic field — `RetrievedChunk.section: str` that
  should have allowed `None` for header-less formats like XLSX/CSV — so when adding a
  new field, check it against every format/provider that might produce it, not just the
  first one tested.)
- Log pipeline stage transitions during ingestion (`parsing`, `chunking`, `embedding`,
  `indexing`) — the frontend sidebar (`project.md` 11.2) depends on these being real,
  observable states, not implied ones.
- New dependencies go through `uv add`, and get reflected in this project's stack table
  in `project.md` Section 4 if they represent a real architectural choice (not a small
  utility). `langchain-groq` and `langchain-google-genai` are pre-authorized per
  Section 4.1 — add them via `uv add`, no further sign-off needed.
- A cancellation or error path must fully reset any state it touched (`isStreaming`-
  style flags, in-flight markers) before returning — never a bare early `return` that
  leaves state half-updated. This is now a named, permanent rule because of a real
  incident, not a hypothetical.

## 5. Coding Conventions — Frontend

- Components stay presentational; state lives in the Zustand stores defined in
  `project.md` Section 8, not scattered in local component state unless it's truly
  local-only (e.g. an input's uncommitted text).
- Every color, spacing, and font choice pulls from the tokens in `project.md` Section
  10 — no ad-hoc hex codes or one-off Tailwind arbitrary values for colors.
- Every state described in Section 11 (empty, loading, processing, error, cancelled)
  must actually be implemented, not left as a TODO — a missing empty state is an
  incomplete component, not a nice-to-have.
- Framer Motion only for the specific interactions listed in Section 9/11 (panel
  slide-in, chip hover, streaming caret, theme cross-fade, upload progress). Don't add
  animation anywhere else "for polish."
- Don't reach for a new UI library or icon set outside what's already in `project.md`
  Section 7 without flagging it first.
- Any SSE/stream-consuming code (`useChatStream.ts`, `lib/sse.ts`) must handle
  cancellation and unexpected stream termination as a first-class case, not an
  afterthought — wrap reader loops so an `AbortError` or an unexpected disconnect
  always reaches the same state-reset path as an explicit cancel.

---

## 6. Verification Protocol

After any non-trivial change, before considering the task finished:

- **Backend:** actually invoke the changed endpoint (via a script, `curl`, or a test) —
  don't rely on "the code compiles / no import errors" as proof of correctness.
- **Ingestion/retrieval changes:** re-run the Stage 2–4 checkpoints against a real
  sample document — retrieval quality regressions are silent and won't show up as
  errors.
- **LLM provider changes:** re-run Stage 10's recalibration sub-steps in full for any
  provider you touch — a provider swap that only confirms "it returns an answer" without
  re-running the grounding/injection/overlap checks is not verified.
- **Frontend:** check the rendered result against the matching wireframe in
  `project.md` Section 11 — pane widths, empty/loading/cancelled states, and citation
  behavior specifically, since these are the details most likely to quietly drift from
  spec. Prefer a real browser session over trusting an automated script alone — this
  project has direct, first-hand evidence that a 12/12-passing script can coexist with a
  visibly broken app if the script only asserts positive presence and never asserts the
  absence of leftover/stuck state.
- **Before any git commit:** run `git status` and read the full file list — if
  `.venv`, `node_modules`, `dist`, or `.env` appear, stop and fix `.gitignore` before
  committing anything.
- **Before declaring a full-stack verification "healthy":** do a genuine cold start
  (Docker down, all dev processes killed, bring everything back up from zero) at least
  once per significant session — a long-lived dev environment can mask problems a fresh
  environment would hit immediately (port conflicts, stale containers, a tracked script
  file gone missing).

---

## 7. When to Ask vs. Proceed

**Proceed without asking when:**
- The task is clearly within a single Build Order stage and fully specified by
  `project.md`.
- A reasonable implementation detail isn't specified (e.g. exact variable names) — pick
  something sensible and move on.
- Adding `langchain-groq` / `langchain-google-genai` as part of Stage 10 — already
  authorized, per Golden Rule 1's documented exception.

**Stop and ask when:**
- The request conflicts with something explicit in `project.md`.
- The request would require building something under a `(V2)` heading.
- A library's actual installed behavior doesn't match what `project.md` assumed — flag
  the mismatch rather than quietly working around it in a way that diverges from the
  documented design.
- You're about to add a new external dependency not already listed or pre-authorized in
  `project.md`.
- A new LLM provider's failure modes don't fit the existing grounding/safety checks and
  you're unsure whether to extend the checks or treat the mismatch as disqualifying for
  that provider — this is a real product-quality decision, not just an implementation
  detail.

---

## 8. Git & Environment Hygiene

- `.gitignore` must include at minimum: `backend/.venv/`, `frontend/node_modules/`,
  `frontend/dist/`, `.env`, `.env.local`, `__pycache__/`, `.pytest_cache/`.
- Verify this file exists and is correct as the very first step of any fresh checkout
  or environment reset.
- Commit messages describe the Build Order stage and what changed, e.g.
  `stage10: add Groq LLMProvider implementation` — this keeps history traceable to the
  plan in Section 3. For multi-part work (a fix plus its regression test plus its
  verification-script update), prefer several small, clearly-labeled commits over one
  large one — this project's history shows small scoped commits are much easier to
  reason about later than a single "fixed everything" commit.
- `docker-compose.yml`'s Postgres port is pinned to `5434:5432` — don't revert this to
  `5432:5432` without first confirming the native Windows Postgres conflict that
  originally caused the pin is no longer present on this machine.
- Never force-push over shared history without explicit confirmation from the user
  first.

---

## 9. Definition of Done — Reference

Don't re-derive this — it already exists in `project.md` Section 16, with the dated,
evidenced scorecard in Section 18. Use it as the actual acceptance criteria. As of this
revision, V1's Definition of Done is **met**, with two items (2 and 4 — streamed
citation answers and the honest refusal path) requiring **re-verification against
whichever LLM provider is actually configured** before that provider is trusted as the
default — this is Stage 10's job, tracked above, not a reopening of V1 itself.