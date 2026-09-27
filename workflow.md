# Agent Workflow — RAG Policy Assistant

> This file governs **how** work gets done. `PROJECT.md` governs **what** gets built
> (scope, stack, schema, UI). Read `PROJECT.md` first, in full, before touching any
> code. If something you're about to do isn't backed by a decision in `PROJECT.md`,
> stop and treat that as missing information — don't invent it.

---

## 1. Golden Rules (never break these)

1. **`PROJECT.md` is the only source of truth for scope, stack, schema, endpoints, and
   UI.** Never introduce a library, service, table, endpoint, or design choice that
   isn't in it. If a task seems to need something outside it, say so explicitly and
   propose adding it to `PROJECT.md` first — don't just add it in code.
2. **Never build anything under a `(V2)` heading in `PROJECT.md`.** Not a stub route,
   not a placeholder table column, not a disabled nav item that references a page that
   doesn't exist yet. If asked to "start on the admin dashboard" or "add comparison,"
   check `PROJECT.md` Section 2's out-of-scope list and say it's V2 before doing anything.
3. **Follow the Build Order in Section 3 of this file in sequence.** Don't jump ahead to
   the LangGraph/reranking layer before hybrid retrieval is proven, and don't start
   frontend polish before the chat endpoint actually returns real answers. Each stage
   has a checkpoint — don't move on until it passes.
4. **Never guess a library's API.** If you're not certain of a function signature,
   parameter name, or return shape for Qdrant, LangGraph, Docling, SQLAlchemy 2.0 async,
   or shadcn/Base UI, check the installed package's actual source/docs (`pip show`,
   reading the installed package, or an official doc lookup) before writing code that
   calls it. Do not pattern-match from a different version's API from memory and assume
   it still applies.
5. **Run and verify before declaring something done.** "This should work" is not a
   completion state. Every backend change gets exercised (a request against the running
   endpoint, or a test). Every frontend change gets checked against the actual rendered
   result reference in `PROJECT.md` Section 11, not just "the code looks right."
6. **Never commit `.venv/`, `node_modules/`, `.env`, or any file over ~10MB.** Check
   `.gitignore` exists and is correct *before* the first commit in any fresh clone or
   fresh venv setup — this project has already had one large-file push rejection from
   this exact mistake; don't repeat it.
7. **Don't touch unrelated files.** A task about the chat endpoint doesn't get to also
   "clean up" the document ingestion module unless that's part of the task. Small,
   scoped diffs only.
8. **When something in `PROJECT.md` conflicts with a request in conversation, ask.**
   Don't silently pick one. Say plainly: "PROJECT.md specifies X for this, you asked
   for Y — which should I follow?"
9. **No secrets, API keys, or credentials in code, commits, or logs.** They come from
   `.env` (backend) or environment injection only, per `PROJECT.md` Section 15.
10. **Don't add error-swallowing (`except: pass`, empty `catch` blocks, silent fallbacks
    that hide failures).** Ingestion and retrieval failures must be visible — a policy
    tool that silently mis-answers is worse than one that visibly errors.

---

## 2. Before Writing Any Code — Checklist

Run through this at the start of every work session, not just the first one:

- [ ] Re-read the relevant section(s) of `PROJECT.md` for this task — don't rely on
      memory of it from earlier in the conversation if it's been a while.
- [ ] Confirm which Build Order stage (Section 3 below) this task belongs to.
- [ ] Confirm the task doesn't touch anything under a `(V2)` heading.
- [ ] Check `git status` — working tree should be clean or only contain the previous
      task's intended changes before starting a new one.
- [ ] If backend: confirm the venv/uv environment is active and dependencies match
      `pyproject.toml` (`uv sync` if unsure) before running anything.
- [ ] If frontend: confirm `npm install` is up to date if `package.json` changed since
      last session.

---

## 3. Build Order — follow this sequence, stage by stage

Do not skip ahead. Each stage ends with a checkpoint that must pass before starting the
next one. This mirrors the debugging-friendly order already established for this
project: prove each layer works in isolation before stacking the next one on top.

### Stage 0 — Skeleton
- FastAPI app boots, `/health` (or root) responds.
- Postgres, Qdrant, Redis containers run via `docker-compose up`.
- React app boots via Vite, renders a placeholder shell.
- **Checkpoint:** `docker compose up` + `npm run dev` both run with no errors, and the
  frontend can successfully call one trivial backend endpoint.

### Stage 1 — Data layer
- SQLAlchemy models for the V1 schema (`PROJECT.md` Section 5) + Alembic migration.
- **Checkpoint:** migration runs clean against a fresh Postgres container; a row can be
  inserted and read back via a quick script or test.

### Stage 2 — Document ingestion (no retrieval yet)
- Upload endpoint saves file to disk, creates a `documents` row with `status=uploading`.
- Docling parses the file into structured elements.
- Chunking by section/heading, metadata attached per chunk (`PROJECT.md` Section 4).
- Dense embedding generated per chunk.
- Chunks + vectors written to Qdrant; `document_chunks` rows written to Postgres;
  `documents.status` progresses to `ready`.
- **Checkpoint:** upload a real sample PDF/DOCX, confirm chunks land in Qdrant with
  correct metadata (inspect via Qdrant's own query, not just "no errors thrown"), and
  `documents.status` reaches `ready`.

### Stage 3 — Retrieval (no LLM yet)
- Dense search against Qdrant.
- Sparse/BM25 search against Qdrant.
- RRF fusion of the two result sets.
- **Checkpoint:** given a known question about the sample document, the fused top-N
  results actually contain the chunk with the answer in it. Check this manually with a
  real query before moving on — don't assume fusion works because it compiles.

### Stage 4 — Reranking + evidence gating
- Cross-encoder reranks the fused candidates.
- Evidence-sufficiency check implemented (`has_sufficient_evidence` logic).
- **Checkpoint:** the correct chunk is now in the top few after reranking (not just
  present somewhere in 30 results), and a deliberately irrelevant question correctly
  triggers the "insufficient evidence" path instead of forcing an answer.

### Stage 5 — LangGraph + LLM generation
- Build the graph exactly as specified in `PROJECT.md` Section 4 (`RAGState`, the node
  sequence, the short-circuit branch).
- LLM generates an answer using only the provided context; citations attached with
  `matched_text` per citation.
- **Checkpoint:** ask the sample question end-to-end via the API, get back an answer
  with correct citations including exact `matched_text`, and confirm a document
  containing adversarial "ignore instructions" text does not get followed.

### Stage 6 — Streaming + chat API
- Wire the graph output through SSE per `PROJECT.md` Section 6.
- Conversation/message persistence.
- **Checkpoint:** a raw `curl`/HTTP client against `/api/v1/chat/stream` shows tokens
  arriving incrementally, final event has the correct JSON shape.

### Stage 7 — Frontend: functional shell before visual polish
- App shell layout (three panes), Zustand stores wired, chat thread renders and
  actually streams from the real backend, citation chips render and are clickable,
  Evidence panel opens with real highlighted `matched_text`.
- **Checkpoint:** the full user flow in `PROJECT.md` Section 16 (Definition of Done,
  items 1–4) works against the real backend — no mocked data left in place.

### Stage 8 — Visual identity pass
- Apply the color system and typography from `PROJECT.md` Section 10.
- Apply the per-component states from Section 11 (empty states, processing states,
  upload flow) — these are not optional extras, they're part of Section 16's Definition
  of Done.
- **Checkpoint:** Definition of Done item 5 and 6 — theme switch works with no layout
  jump, and the result doesn't read as a generic AI-generated dashboard.

### Stage 9 — Hardening pass (still V1, not V2)
- Basic error states surfaced in the UI (upload failure, LLM timeout, empty document
  list edge cases).
- Re-check Section 1's golden rules haven't drifted (no secrets committed, no V2 code
  present, `.gitignore` still correct).

**Only after Stage 9 is genuinely complete** should Section 13 (Comparison) or Section
14 (Admin Dashboard) in `PROJECT.md` be started, and only when explicitly requested.

---

## 4. Coding Conventions — Backend

- Async all the way through (`async def` routes, async SQLAlchemy session, async Qdrant
  client) — don't mix sync calls into the async request path.
- One responsibility per module: a route handler calls a service function; the service
  function calls the RAG graph / repository layer. Don't put business logic directly in
  route handlers.
- Every Pydantic model used in a request/response gets explicit field types — no bare
  `dict` payloads for anything the frontend depends on.
- Log pipeline stage transitions during ingestion (`parsing`, `chunking`, `embedding`,
  `indexing`) — the frontend sidebar (`PROJECT.md` 11.2) depends on these being real,
  observable states, not implied ones.
- New dependencies go through `uv add`, and get reflected in this project's stack table
  in `PROJECT.md` Section 4 if they represent a real architectural choice (not a small
  utility).

## 5. Coding Conventions — Frontend

- Components stay presentational; state lives in the Zustand stores defined in
  `PROJECT.md` Section 8, not scattered in local component state unless it's truly
  local-only (e.g. an input's uncommitted text).
- Every color, spacing, and font choice pulls from the tokens in `PROJECT.md` Section
  10 — no ad-hoc hex codes or one-off Tailwind arbitrary values for colors.
- Every state described in Section 11 (empty, loading, processing, error) must actually
  be implemented, not left as a TODO — a missing empty state is an incomplete component,
  not a nice-to-have.
- Framer Motion only for the specific interactions listed in Section 9/11 (panel
  slide-in, chip hover, streaming caret, theme cross-fade, upload progress). Don't add
  animation anywhere else "for polish."
- Don't reach for a new UI library or icon set outside what's already in `PROJECT.md`
  Section 7 without flagging it first.

---

## 6. Verification Protocol

After any non-trivial change, before considering the task finished:

- **Backend:** actually invoke the changed endpoint (via a script, `curl`, or a test) —
  don't rely on "the code compiles / no import errors" as proof of correctness.
- **Ingestion/retrieval changes:** re-run the Stage 2–4 checkpoints from Section 3 above
  against a real sample document — retrieval quality regressions are silent and won't
  show up as errors.
- **Frontend:** check the rendered result against the matching wireframe in `PROJECT.md`
  Section 11 — pane widths, empty/loading states, and citation behavior specifically,
  since these are the details most likely to quietly drift from spec.
- **Before any git commit:** run `git status` and read the full file list — if
  `.venv`, `node_modules`, `dist`, or `.env` appear, stop and fix `.gitignore` before
  committing anything.

---

## 7. When to Ask vs. Proceed

**Proceed without asking when:**
- The task is clearly within a single Build Order stage and fully specified by
  `PROJECT.md`.
- A reasonable implementation detail isn't specified (e.g. exact variable names) — pick
  something sensible and move on.

**Stop and ask when:**
- The request conflicts with something explicit in `PROJECT.md`.
- The request would require building something under a `(V2)` heading.
- A library's actual installed behavior doesn't match what `PROJECT.md` assumed (e.g. a
  Qdrant API shape differs from what Section 4 implies) — flag the mismatch rather than
  quietly working around it in a way that diverges from the documented design.
- You're about to add a new external dependency not listed in `PROJECT.md`.

---

## 8. Git & Environment Hygiene

- `.gitignore` must include at minimum: `backend/.venv/`, `frontend/node_modules/`,
  `frontend/dist/`, `.env`, `.env.local`, `__pycache__/`, `.pytest_cache/`.
- Verify this file exists and is correct as the very first step of any fresh checkout
  or environment reset — this has already caused one failed push in this project.
- Commit messages describe the Build Order stage and what changed, e.g.
  `stage3: add RRF fusion for hybrid retrieval` — this keeps history traceable to the
  plan in Section 3.
- Never force-push over shared history without explicit confirmation from the user
  first.

---

## 9. Definition of Done — Reference

Don't re-derive this — it already exists in `PROJECT.md` Section 16. Use it as the
actual acceptance criteria for calling V1 complete. This workflow file exists to get you
there without wrong turns, not to redefine what "done" means.
