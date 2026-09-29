# Backend Status Report — RAG Policy Assistant

**Generated:** 2026-09-27, by an agent with full read/terminal access.
**Scope:** `backend/` only. Every claim below was checked in the reporting session unless
explicitly marked *unverified*. Spec references are to `project.md` (Sections numbered
1–17) and `workflow.md` (Sections numbered 1–9) — note both files are **lowercase**
(`project.md`, `workflow.md`), not `PROJECT.md`/`WORKFLOW.md`.

A prior snapshot exists at `backend/docs/status-2026-09-27.md` (948 lines, commit
`7528a965`). It is **not** a substitute for this file and was not relied on except where
explicitly cited.

---

## 1. Build Order status (maps to `workflow.md` Section 3)

| Stage | Status | What was actually verified this session |
|---|---|---|
| **0 — Skeleton** | **DONE** (backend) | `uv run python run.py` via `scripts/start_server.ps1` → `GET /health` returns `status: "ok"` with postgres 6.88 ms / qdrant 7.22 ms / redis 11.62 ms, all `ok:true`. Compose services up. Frontend half of the checkpoint (`npm run dev` + frontend calling a backend endpoint) is **unverified** — not run this session. |
| **1 — Data layer** | **DONE** | 7 tables live in Postgres; columns match `project.md` §5 exactly (§3). Models in `app/db/models.py` match the live schema, no drift. Rows read back (see §3). |
| **2 — Ingestion** | **PARTIAL** | **PDF** verified end-to-end: `acme_travel_policy.pdf` → `status=ready`, 13 chunks in Qdrant + 13 `document_chunks` rows, 3 of them `content_type=table` carrying real markdown pipe-tables. **DOCX** verified end-to-end *this session* via `verify_injection` (uploaded adversarial .docx → `status: ready` → answered → purged). **XLSX and CSV: never ingested end to end** — `app/ingestion/parser.py:27-30` maps all four suffixes to Docling `InputFormat`, but no run has ever produced an XLSX/CSV document row. Missing: XLSX + CSV evidence, plus cleanup of one stale `failed` docx row. |
| **3 — Retrieval** | **DONE** | `scripts/verify_stage3.py` exists (16 KB) as the Stage 3 checkpoint. Live cross-check performed this session: Qdrant `points_count = 13` == `document_chunks = 13`. RRF fusion live in `app/services/retrieval.py`. |
| **4 — Rerank + evidence gate** | **DONE** | Cross-encoder `cross-encoder/ms-marco-MiniLM-L-6-v2`, threshold `evidence_min_score = 0.0`, calibrated by `scripts/calibrate_evidence.py` (measured recall 6/7, precision 8/9 — comment at `config.py:118-127`). Gate behaviour re-confirmed live this session: "What is the nightly accommodation cap in New York?" returned `status=gate-declined` — the known, documented false negative (table-text recall miss, scores −1.89). |
| **5 — LangGraph + LLM generation** | **PARTIAL** | Graph + short-circuit branch + citations with `matched_text` all present and exercised. Injection safeguard **PASSED 10/10 this session** (1 run). **But the generation-quality gate is not met**: in a partial `verify_generation` run this session, "What is the daily meal allowance for an international trip?" answered `100 USD` with **`citations=0`** — the exact defect FIX-1 targets is still reproducing. See §7. |
| **6 — Streaming + chat API** | **DONE (endpoint), partially re-verified** | `POST /api/v1/chat/stream` present in live `openapi.json`. `scripts/verify_stage6.py` (13.7 KB) is the checkpoint harness for incremental-SSE + final-event-shape + Postgres persistence. **This session** the endpoint was exercised for real by `verify_generation` (token + done events parsed, answers + citations returned) but **`verify_stage6` itself was not re-run**, so the "tokens arrive spread over time, not one burst" assertion is **unverified this session**. Conversations/messages/citations persistence is real (149/292/98 rows). |
| **7 — Frontend functional shell** | **NOT STARTED** | See the frontend note below. No streaming, no citation chips, no evidence panel, none of `project.md` §16 items 1–4. |
| **8 — Visual identity pass** | **NOT STARTED** | No `themeStore.ts`, no `components/layout/*`, no Espresso/Ivory token files exist. `App.css` / `index.css` contents were **not read** this session, so "no palette work yet" is inferred from absent files, not from CSS inspection. |
| **9 — Hardening pass** | **PARTIAL** | Some error surfacing exists (typed `error` contract in `RAGState`, `GENERATION_FAILED_ANSWER` / `GENERATION_REJECTED_ANSWER` refusals, per-dependency `/health`). No frontend error states (Stage 7 not started). Golden-rule re-check not formally done. |

**Frontend, one line as requested — but the premise needs correcting:** the frontend is
**not** an untouched Vite/shadcn scaffold. `frontend/src/` contains `pages/Chat.tsx`,
`pages/Documents.tsx`, `pages/Home.tsx`, `stores/chatStore.ts`, `types/chat.ts`,
`types/document.ts`, `lib/api.ts` and 9 `components/ui/*` shadcn primitives. What is
missing versus `project.md` §12: `stores/{workspace,evidence,theme}Store.ts`,
`components/{chat,evidence,documents,layout}/*`, `hooks/{useChatStream,useUploadDocument}.ts`,
`lib/sse.ts`, `types/citation.ts`. All of it is committed (clean working tree for
`frontend/`); none of it is wired to the backend.

---

## 2. Environment & tooling facts

### Versions and lockfile state

| Item | Value |
|---|---|
| Python | **3.11.9** (`uv run python -V`) |
| uv | **0.11.21** (5aa65dd7a 2026-06-11 x86_64-pc-windows-msvc) |
| Project env | `backend/.venv` |
| Lockfile | `backend/uv.lock`, **in sync** — `uv sync --dry-run` → "Found up-to-date lockfile", "Resolved 194 packages", "Checked 168 packages", **"Would make no changes"** |
| Platform | win32 / Windows PowerShell 5.1 |

### The startup procedure that works

```powershell
powershell -ExecutionPolicy Bypass -File backend\scripts\start_server.ps1
```

This is the only sanctioned entry point. It clears port 8000, launches
`uv run python run.py` **detached**, polls `/health` for up to 180 s, prints dependency
status, and exits 1 if any dependency is unhealthy. Logs →
`%TEMP%\rag_policy_logs\server.{out,err}.log`.

### Known footguns (each has already cost real time — do not rediscover)

1. **Never call `uvicorn` directly.** `run.py` installs the Windows **selector** event loop
   (`app/core/eventloop.py`). psycopg async cannot run on the default Windows
   `ProactorEventLoop`. uvicorn 0.36+ uses a loop *factory*, not a policy, so
   `selector_loop_factory` is passed explicitly and `configure_event_loop()` covers
   non-uvicorn entry points (Alembic, scripts).
2. **`RELOAD=false` is mandatory in `.env`.** `start_server.ps1` throws if it isn't. Ingestion
   runs in-process in V1 (no worker queue per §2), so dev auto-reload cancels in-flight
   ingestion. Note the trap documented in `eventloop.py`: reload mode is what makes
   uvicorn's *built-in* factory return a selector loop, so turning reload off would break
   psycopg if the explicit factory weren't passed.
3. **Never `cd backend` + `Start-Process ... run.py`.** `Start-Process` does not inherit the
   caller's working directory, so `run.py` resolves to nothing and the process dies before
   binding — with only a bare stderr line and no listener to notice. The script passes an
   absolute `-WorkingDirectory` derived from `$PSScriptRoot`.
4. **Never add `-Redirect*` to that `Start-Process`.** With a redirect, PowerShell switches
   to `CreateProcess(bInheritHandles=TRUE)`, duplicating the calling harness's pipes into
   the detached child; the caller blocks forever on a pipe that never reaches EOF even
   though the script exited 0. The script deliberately uses `cmd /c` with redirection
   *inside* the child and `-WindowStyle Hidden` instead.
5. **Stack is not persistent across sessions.** This session both Docker Desktop and Ollama
   were down at the start. Correct order: start Docker Desktop → wait for the daemon →
   `docker compose up -d postgres qdrant redis` → start Ollama (`ollama serve`) →
   `start_server.ps1`. Ollama is a **host** process, not a container.
6. **The Visual C++ redistributable banner** ("Microsoft Visual C++ Redistributable is not
   installed…") prints on stderr for every `uv run python` invocation in this venv. It is
   **cosmetic** — DOCX ingestion succeeded end-to-end this session despite it (§7).

### `.env` / `.env.example` state

`backend/.env` — 10 variables, **all with real values, none placeholders** (values not
reproduced; see below for the non-secret ones):

| Variable | Set | Value / note |
|---|---|---|
| `APP_NAME` | yes | non-secret |
| `ENVIRONMENT` | yes | `development` |
| `DATABASE_URL` | yes | async driver `postgresql+psycopg://***@localhost:5432/rag_policy` (dev creds) |
| `QDRANT_URL` | yes | `http://localhost:6333` |
| `REDIS_URL` | yes | `redis://localhost:6379/0` |
| `EMBEDDING_MODEL` | yes | `sentence-transformers/all-MiniLM-L6-v2` |
| `LLM_PROVIDER` | yes | `ollama` |
| `OLLAMA_BASE_URL` | yes | `http://localhost:11434` |
| `OLLAMA_MODEL` | yes | `qwen2.5:3b` |
| `RELOAD` | yes | `false` |

**Drift between `.env` and `.env.example` (10 vs 10 keys, but different sets):**
- In `.env.example` but **not** in `.env`: `QDRANT_COLLECTION`, `STORAGE_DIR`
- In `.env` but **not** in `.env.example`: `OLLAMA_BASE_URL`, `OLLAMA_MODEL`
  (i.e. the Ollama model — the single most important setting — is undocumented for a fresh clone)

`project.md` §15 lists only 5 variables (`DATABASE_URL`, `QDRANT_URL`, `REDIS_URL`,
`LLM_PROVIDER`, `EMBEDDING_MODEL`); the real set is larger. A `field_validator` on
`database_url` (`config.py:161`) **rejects** any URL without `+psycopg`/`+asyncpg`.

### Docker services: expected vs actually running

| Compose service (`docker-compose.yml`) | Status |
|---|---|
| `postgres` (`rag_policy_postgres`, postgres:16-alpine) | **running**, `0.0.0.0:5432→5432` — this is the one the app uses |
| `qdrant` (`rag_policy_qdrant`) | **running**, `6333-6334` |
| `redis` (`rag_policy_redis`, redis:7-alpine) | **running**, `6379` |
| `minio` | **cannot start** — `docker compose up` fails with *"pull access denied for minio/minio, repository does not exist"*. Also **unused by design**: §2 puts uploads on local disk under `backend/storage/`. Effectively dead config. |
| `backend` | **not used** — backend runs natively on the host via `start_server.ps1` |
| `frontend` | **not used** — not started this session |

**Running but not in `docker-compose.yml`:** `pgadmin` (dpage/pgadmin4, port 5050) and a
second `postgres` (postgres:18) published on **5433**. Neither is referenced by the app;
`rag_policy_postgres` owns 5432.

**Ollama is not in `docker-compose.yml` at all** — it runs on the Windows host
(`%LOCALAPPDATA%\Programs\Ollama\ollama.exe serve`). Models currently pulled:
`qwen2.5:3b` (Q4_K_M, 1.93 GB) and `qwen2.5:1.5b` (Q4_K_M, 986 MB). **`llama3.2:3b` and
`phi3.5` are NOT pulled** — the deferred model-benchmark comparison cannot run without
pulling them first.

---

## 3. Database state (introspected live, 2026-09-27)

### Tables (7, from `\dt`)

| Table | Columns (live, from `information_schema`) |
|---|---|
| `users` | `id uuid NOT NULL`, `email varchar NOT NULL`, `name varchar NOT NULL` |
| `documents` | `id uuid NOT NULL`, `filename varchar NOT NULL`, `file_type varchar NOT NULL`, `storage_path varchar NOT NULL`, `status varchar NOT NULL`, `created_at timestamptz NOT NULL default now()` |
| `document_chunks` | `id uuid NOT NULL`, `document_id uuid NOT NULL`, `chunk_index integer NOT NULL`, `page_number integer NULL`, `section varchar NULL`, `qdrant_point_id varchar NOT NULL` |
| `conversations` | `id uuid NOT NULL`, `user_id uuid NOT NULL`, `title varchar NOT NULL`, `created_at timestamptz NOT NULL default now()` |
| `messages` | `id uuid NOT NULL`, `conversation_id uuid NOT NULL`, `role varchar NOT NULL`, `content text NOT NULL`, `created_at timestamptz NOT NULL default now()` |
| `citations` | `id uuid NOT NULL`, `message_id uuid NOT NULL`, `document_id uuid NOT NULL`, `page_number integer NULL`, `section varchar NULL`, `relevance_score double precision NOT NULL`, `matched_text text NULL` |
| `alembic_version` | `version_num varchar NOT NULL` |

**Matches `project.md` §5 exactly**, including the one addition §6 requires
(`citations.matched_text`). No `permissions`, `document_versions` or `audit_logs` tables —
correct for V1 (§5). `app/db/models.py` columns match the live schema; **no drift found**.

**Expected and worth knowing:** `document_chunks` has **no text column**. Chunk text lives
only in the Qdrant payload and is joined back via `qdrant_point_id` — this is intentional
and documented in `models.py:7-10`, and is consistent with §5 as written. (A report that
queries `content` on this table will fail; I hit exactly that.)

### Alembic history

Applied head: **`6c1d4a8b2f70`**. Migration files in `backend/alembic/versions/`:

| Revision | File |
|---|---|
| `ab057e3ff53a` | `stage1_initial_v1_schema_users_.py` |
| `6c1d4a8b2f70` | `stage6_citations_matched_text.py` (head, applied) |

### Row counts

| Table | Rows |
|---|---|
| `users` | 1 |
| `documents` | 2 |
| `document_chunks` | 13 |
| `conversations` | 149 |
| `messages` | 292 |
| `citations` | 98 |

### `documents` — full enumeration (2 rows)

| id | filename | file_type | status | created_at (UTC) | chunk_count |
|---|---|---|---|---|---|
| `73d7e9b1-ebd9-47a3-b5d5-4dd91702c498` | `acme_travel_policy.pdf` | pdf | **ready** | 2026-09-27 07:14:26 | 13 |
| `8acfbf67-cdfa-422a-a5aa-fdcde37380a6` | `acme_travel_policy.docx` | docx | **failed** | 2026-09-27 07:32:56 | 0 |

The `failed` docx row is **stale and unexplained** — DOCX ingestion demonstrably works
(`verify_injection` uploaded and ingested a .docx successfully this session, reaching
`ready` and producing 2 chunks). The failure reason for this specific row is **unverified**;
`documents` has no error-message column. This row is also the only `status != ready` row and
would be the natural first cleanup target.

### Qdrant

| Property | Value |
|---|---|
| Collection | **`policy_chunks`** (from `settings.qdrant_collection`, `config.py:149`) |
| Status / optimizer | `green` / `ok` |
| Dense vector | size **384**, distance **Cosine** (derived from the loaded embedding model, not hardcoded) |
| Sparse vector | named `sparse` (BM25, `app/services/bm25.py`), on-disk index enabled by default |
| HNSW | `m=16`, `ef_construct=100` |
| **points_count** | **13** |
| indexed_vectors_count | 13 |
| segments | 8 |
| payload indexes | `filename` (keyword), `document_id` (keyword) |
| quantization | none |

**Cross-check performed this session:** Qdrant `points_count = 13` == Postgres
`document_chunks = 13`. **Consistent.** (Re-verified *after* the injection test self-cleaned:
still 13 = 13.)

**Chunk inventory (scrolled from Qdrant, text intact):** 13 chunks, `chunk_index` 0–12,
pages 1–2, of which **3 are `content_type=table`** (`idx` 4, 8, 9) and 10 are `text`.
Representative table chunk (`idx=8`, page 2) — the per-destination meal-allowance table:

```
| Destination           |   Daily allowance (USD) | Currency       |
|-----------------------|-------------------------|----------------|
| New Yo...              | ...
```

`idx=9` is the nightly accommodation cap table (`City band | Examples | Nightly cap (USD)`),
`idx=4` is `Table 2.2a: Leave accrual rates`. **Table-preservation-as-markdown is verified
working in live stored data**, not just in code.

---

## 4. API surface actually implemented

Enumerated from the **live** `GET /openapi.json`, not from source reading alone.
All routes are mounted in `main.py:55-58`.

| Method | Path | Request | Response | Exercised with a real request? |
|---|---|---|---|---|
| GET | `/health` | — | `HealthResponse{status, app, environment, dependencies[{name, ok, latency_ms, detail}]}` | **Yes, this session** — all 3 deps green |
| POST | `/api/v1/documents` | multipart file upload | `DocumentUploadResponse{document_id, status}` | Yes — `verify_injection` uploaded a real .docx this session (`status: ready`) |
| GET | `/api/v1/documents` | — | `DocumentListResponse{documents[DocumentSummary], total}` | **Yes, this session** — returned both rows, `chunk_count` 13 / 0 |
| GET | `/api/v1/documents/{document_id}` | — | `DocumentDetail` = `DocumentSummary` + `stage_index`, `stage_total` | Yes — `verify_injection` polls status via this shape; not re-called by hand this session |
| POST | `/api/v1/chat/stream` | `ChatRequest{query (1–4000 chars), conversation_id?, document_ids?}` | SSE: `token` (`TokenEvent`), `done` (`ChatFinal`), `error` (`ErrorEvent`) | **Yes, this session** — 6 questions streamed successfully in a partial `verify_generation` run |
| GET | `/api/v1/conversations` | — | `ConversationListResponse` | Yes historically (`verify_stage6` check 6); not re-called this session |
| GET | `/api/v1/conversations/{conversation_id}` | — | `ConversationDetail` = summary + `messages[MessageOut{…, citations[Citation]}]` | Yes historically (`verify_stage6` check 6); not re-called this session |
| POST | `/api/v1/_debug/search` | `SearchRequest{query, limit≤50, document_ids?, candidate_limit?}` | `HybridSearchResponse` (includes `RetrievedChunk{score, heading_path, dense_score, sparse_score}`, `ArmTimings`) | **Yes, this session** — `verify_generation.retrieved_blocks()` calls it per question |
| POST | `/api/v1/_debug/ask` | `ChatRequest`-like | answer + `outcome`, `attempts`, `retried`, `generation_stats[]` | **Not exercised this session.** Note: this endpoint is backed by **uncommitted** `app/api/debug.py` changes — see §8 |

`Citation` shape (from `app/schemas/chat.py:17-31`): `id` ("c1"), `document_id`,
`document_name`, `page` (nullable — "null for flow formats"), `section`, `matched_text`
(**required by §6** for highlighting), `relevance` — where `relevance = sigmoid(cross_encoder_logit)`
(`citations.py:15-17`), which is a **deviation from §6**, which shows `"relevance": 0.91`
as if it were a similarity score. See §7.

### `project.md` §6 routes that do **NOT** exist

| Missing route | Consequence |
|---|---|
| `GET /api/v1/documents/{id}/pages/{page}` | **The evidence-viewer endpoint is absent.** §6 says it returns "page text + bounding boxes for highlight", and §1's non-negotiable UI rule plus §16 item 3 both depend on it. This is the single largest functional gap on the backend: citation chips can be rendered, but there is no way for the frontend to fetch the page to highlight. (This is the "Phase 2" item deferred from the last session.) |

Nothing from §13 (comparisons) or §14 (admin) exists — **correct**, both are V2 and
`workflow.md` Golden Rule 2 forbids scaffolding them. Verified: no `/api/v1/comparisons`,
no `/api/v1/admin/*`, no `document_versions` / `query_logs` tables, no `users.role` column.

---

## 5. RAG pipeline — component-by-component

### Ingestion

| Format | Parser mapping | End-to-end evidence |
|---|---|---|
| PDF | `parser.py:27` → `InputFormat.PDF` | **Yes** — 13 chunks, 3 markdown tables, `status=ready` |
| DOCX | `parser.py:28` → `InputFormat.DOCX` | **Yes, this session** — `verify_injection` DOCX → `ready`, answered, then purged 2 chunks |
| XLSX | `parser.py:29` → `InputFormat.XLSX` | **No — never run end to end** |
| CSV | `parser.py:30` → `InputFormat.CSV` | **No — never run end to end** |

§2 requires all four in V1. **XLSX and CSV are the open ingestion gap**, and it is
worth noting that XLSX/CSV are exactly the formats where §4's "tables kept as markdown
tables" requirement is hardest, so this is not a trivial smoke test.

**Visual C++ redistributable warning:** it appears on stderr for every `uv run python`
in this venv. It has **never caused a functional failure** — DOCX ingestion completed
successfully with it present. The one historical ingestion failure recorded against it
was the dev-reload cancellation, which is a different root cause (documented at
`config.py:155-159`). Current status: **warning only, no functional impact observed.**

### Chunking

- Chunker: `app/ingestion/chunker.py`. Chunks by section/heading, each carrying a heading
  breadcrumb; tables emitted as **standalone chunks** with `content_type="table"`
  (`chunker.py:192-218`), never flattened to prose.
- Markdown emission via `item.export_to_markdown(doc=document)` (`chunker.py:128-130`) —
  the `doc=` argument is **required** by the installed docling-core; omitting it fails.
- **Check that verifies it: the live Qdrant scroll in §3.** 3 of 13 stored chunks are real
  pipe-delimited markdown tables with header separator rows. Also `scripts/test_metadata_checks.py`
  and `verify_stage2.py` cover metadata.
- `app/ingestion/chunking.py` is an **empty 0-byte leftover file** — see §7.

### Retrieval

| Constant | Value | Location |
|---|---|---|
| `RRF_K` | **60** (the original RRF paper value) | `app/services/retrieval.py:34` |
| Dense candidate limit | 30 | `config.py:107` (`rerank_candidate_limit`) |
| Fused `limit` | default 10, max 50 | `app/schemas/retrieval.py:17` |
| Sparse | BM25 via Qdrant `SparseVector` | `app/services/bm25.py` |

`retrieval.py:32-33` documents why 60 was kept rather than tuned. `RetrievedChunk` also
returns `dense_score` and `sparse_score` separately, so per-arm behaviour is inspectable.

### Reranking + evidence gate

- Cross-encoder: **`cross-encoder/ms-marco-MiniLM-L-6-v2`**, `device="cpu"`
  (`config.py:106`, `app/services/reranker.py:42`).
- 30 candidates → **top 8** (`config.py:107-108`), batch 16.
- **`evidence_min_score = 0.0`** (`config.py:128`), `evidence_min_chunks = 1`.
  Justification, verbatim intent from the comment: 0.0 is the cross-encoder's own decision
  boundary (logit 0 = sigmoid 0.5 = "more likely relevant than not"), chosen **rather than a
  corpus-fitted value so the gate does not encode the shape of the 13 chunks that happened
  to be ingested**. `calibrate_evidence.py` measured recall 6/7, precision 8/9, with both
  misses documented rather than tuned away.
- **Known live false negative, re-confirmed this session:** "What is the nightly
  accommodation cap in New York?" → `status=gate-declined`, refusal returned, LLM never
  called. Cause per the comment: a prose question against a markdown table scores −1.89
  because the encoder reads table text, not schema.
- **Known live false positive:** "part-time employees sick leave" scores **+8.36** against a
  full-time-only passage. A high score means "about that topic", never "answers your
  question".

### Generation

- **Provider: local Ollama, `qwen2.5:3b` (Q4_K_M), confirmed live** — `/api/tags` returns
  it and `/health` is green with the server generating real answers this session.
- Parameters actually in use (`app/services/llm.py:186-199`, `config.py:87-100`):

| Param | Value | Note |
|---|---|---|
| `temperature` | **0.0** | |
| `num_ctx` | **2048** | was 4096; reduced in the uncommitted-then-committed latency work. Live prompt ≈1363 tokens, so 2048 is sufficient |
| `num_predict` | **300** | hard ceiling; measured answers are 16–47 tokens, so it only bounds pathological rambling |
| `keep_alive` | **"30m"** | Ollama's own default is 5m |
| `timeout` | 180 s | |
| `format mode` | **none / plain text** — not JSON, not structured; the model emits `[n]` markers in prose, which `citations.py` parses and strips |
| `top_p` | **not set** (Ollama default) | |

- **Restatement / verbatim-copying issue: MITIGATED, with measured enforcement — not
  "fixed" in the sense of being impossible.** The mechanism is real and testable:
  `app/rag/restatement.py` computes 8-gram containment between the answer and every
  retrieved block, `MAX_OVERLAP = 0.30` (calibrated: genuine synthesised answers measured
  0.00–0.245, confirmed restatements 0.41–1.00, ceiling placed in the gap), and the graph
  **rejects and silently retries** an answer that exceeds it. `scripts/verify_generation.py`
  re-asserts the same ceiling from outside the request path.
  **Latest `verify_injection.py` result (run this session, 1 run): 10/10 checks passed**,
  including `answer does not restate a block (overlap < 0.3)` at a measured overlap of
  **0.0000** (worst block 0, longest run 7 words), and all 5 injected compliance strings
  absent, with the adversarial document fully removed afterwards.
  This is a **single run** — the script has no `--runs` flag (no `add_argument` calls at
  all), so "pass rate across N runs" is **1/1, unverified beyond that**.
  Honest residual: the model *can* still restate; the guard detects and retries it. Whether
  the retry always converges is what `verify_generation --runs N` measures, and see below.

### Streaming

- `POST /api/v1/chat/stream` **exists and works** — confirmed in live `openapi.json` and
  exercised for real this session (6 questions streamed; token + done events parsed).
- **The current implementation is validate-then-replay, not true token streaming.**
  `graph.py` collects the first generation *silently* (never touching the token sink),
  validates the full text, and only then replays the accepted answer to the client piece
  by piece at a matched cadence (`_collect` / `_replay`, `graph.py:268-291`, `425-518`).
  A rejected attempt is retried silently too. **The client never sees a token from an
  answer that failed validation** — deliberate and correct for a policy tool.
  **Consequence a future agent must know:** time-to-first-token is *not* reduced by this
  design; the user waits for the whole generation before seeing anything. Whether tokens
  then *appear* spread over time (vs one burst) is exactly what `verify_stage6.py` check 1
  asserts, and **that script was not re-run this session — unverified.**

---

## 6. Test suite & verification scripts

### `uv run pytest -q` — run this session

```
106 passed in 19.67s
```

Zero failures, zero skips, zero warnings. Per-file collected counts:

| File | Tests |
|---|---|
| `tests/test_grounding.py` | **20** (new in `65adf88d`) |
| `tests/test_evidence.py` | 11 (new in `65adf88d`) |
| `tests/test_citations.py` | 12 |
| `tests/test_restatement.py` | 17 |
| `tests/test_documents.py` | 12 |
| `tests/test_config.py` | 10 |
| `tests/test_bm25.py` | 9 |
| `tests/test_retrieval_rrf.py` | 8 |
| `tests/test_eventloop.py` | 7 |
| **Total** | **106** |

All tests are unit-level (no live-server integration tests in `pytest`); end-to-end proof
comes from the `scripts/` harness below.

### Permanent verification scripts (`backend/scripts/`)

| Script | Proves |
|---|---|
| `verify_stage1.py` | Stage 1 checkpoint: models + migration, a row written and read back |
| `verify_stage2.py` | Stage 2 checkpoint: upload → Docling → chunks in **Qdrant inspected directly** (not "no errors") → `status=ready`, with correct metadata |
| `verify_stage3.py` | Stage 3 checkpoint: fused top-N actually contains the answering chunk for a known question; per-arm dense/sparse scores |
| `verify_stage6.py` | Stage 6 checkpoint: tokens arrive **spread over time** (not one burst), final event has exactly §6's keys, every `matched_text` is a literal substring **re-read from Qdrant**, irrelevant question emits zero token events, and conversations/messages/citations are really in Postgres |
| `verify_injection.py` | Prompt-injection safeguard: uploads an adversarial DOCX, asserts no injected compliance string, asserts the answer reflects real content, asserts full cleanup from both stores |
| `verify_generation.py` | Answer *quality* at the API layer: non-refusal, ≥1 citation, not a numbered restatement, verbatim-overlap ceiling, **and that the answer contains the documented facts**. Supports `--runs N`, `--question-index`, `--max-overlap`, `--log` |
| `verify_orphan_cleanup.py` | No orphaned Qdrant vectors after a failed/aborted ingestion (the two-layer purge) |
| `calibrate_evidence.py` | Derives `evidence_min_score`; source of the recorded recall 6/7, precision 8/9 |
| `test_metadata_checks.py` | Chunk metadata conformance checks |
| `make_sample_pdf.py` / `make_sample_doc.py` / `policy_content.py` | Fixtures: generate the sample policy in both formats and hold its canonical text |
| `cleanup_simulated_rows.py` | Removes rows written by simulated/failed ingestion runs |
| `measure_llm_latency.py` | **Untracked.** Per-request latency breakdown of the LLM path |
| `measure_llm_options.py` | **Untracked.** Sweeps Ollama `num_ctx` / `num_predict` / `keep_alive` and measures eviction cost |

**No temporary `_tmp*.py`, `tmp_*.py`, `_probe*.py` or `scratch*.py` files exist anywhere in
the repo** (searched recursively, excluding `.venv` and `node_modules`). Clean.

### Currently-failing or incomplete checks

1. **`verify_generation` — the meal-allowance citation defect is still reproducing.**
   A run started this session was **interrupted after 6 of 10 questions of run 1 of 3**
   (the session pivoted to reporting). Real captured output:

   | Question | status | citations | Verdict |
   |---|---|---|---|
   | annual leave / cap | answered | 1 | pass |
   | **nightly accommodation cap NY** | **gate-declined** | 0 | known Stage 4 recall miss |
   | **business class, 5h flight** | answered | **1** | **FIX-2 working** — answer: *"No, according to, economy class must be booked for all flights under 6 hours. Business class requires written director sign-off."* |
   | **daily meal allowance** | answered | **0** | **FIX-1 NOT working** — answer *"The daily meal allowance for an international trip is 100 USD."* is **correct** but carries **no citation** |
   | expense claim window | answered | 1 | pass |
   | parental leave | answered | 1 | pass |

   So: **FIX-2 confirmed working on 1 sample; FIX-1 confirmed still broken on 1 sample.**
   The required 3-consecutive-run gate has **not** been met and **no 3-run evidence exists
   for the current code**. The prior snapshot (`7528a965`) recorded 260/270 over 5 runs with
   these same two questions failing in all five — i.e. FIX-1/FIX-2 target a *known,
   reproducible* defect, and FIX-1 has not yet demonstrably fixed it.
2. **Cosmetic-answer defect visible in the FIX-2 output:** the retry produced
   *"No, according to, economy class must be booked…"* — a dangling `according to,` left
   where the citation marker was stripped. Grammatically broken user-facing text. Not
   covered by any assertion.
3. **`verify_stage6.py` not re-run this session** → its incremental-SSE and
   final-event-shape assertions are unverified for the current code.

---

## 7. Known issues, technical debt, deviations

### Fixed earlier — still in place, worth remembering (one line each)

- **Orphaned Qdrant vectors on failed ingestion — fix INTACT.** `app/services/chunk_purge.py`
  purges **both** stores in one call: Qdrant points by **payload filter on `document_id`**
  (not a list of known ids, deliberately, so startup reconciliation works after a crash
  with no id list in memory) **and** the `document_chunks` rows, then invalidates the cached
  BM25 IDF via `corpus_stats.invalidate()`. Verified live this session: after
  `verify_injection` purged its document, counts returned to `documents=2`,
  `document_chunks=13`, Qdrant `points_count=13`. Cross-check holds.
- **Windows event-loop breakage** — `app/core/eventloop.py` handles both the uvicorn
  loop-factory path and the `asyncio.run` path; covered by `tests/test_eventloop.py` (7 tests).
- **Dev auto-reload cancelling in-flight ingestion** — `RELOAD=false` is now enforced by
  `start_server.ps1`, which throws rather than warns.

### Open / deferred

| # | Item | Status |
|---|---|---|
| 1 | **FIX-1 (content attribution) not working** | Meal-allowance answer correct but `citations=0`. The full 3-run gate is unmet. **Highest priority.** |
| 2 | **`GET /api/v1/documents/{id}/pages/{page}` missing** | Blocks §16 item 3 (evidence viewer). Deferred as "Phase 2". |
| 3 | **XLSX + CSV never ingested end to end** | Required by §2. Parser mappings exist; no evidence. |
| 4 | **Nightly accommodation cap recall miss** | Reproducible gate false negative (−1.89 on table text). Documented, not tuned away. |
| 5 | **Part-time sick-leave false positive (+8.36)** | Known cross-encoder behaviour; only generation-stage no-extrapolation catches it. |
| 6 | **Stale `failed` docx document row** | Unexplained; DOCX otherwise works. No error column to explain it. |
| 7 | **Model benchmarking (qwen2.5:1.5b / llama3.2:3b / phi3.5)** | Explicitly deferred. `llama3.2:3b` and `phi3.5` are not even pulled locally. |
| 8 | **Latency sweep results inconclusive** | `measure_llm_options.py` crashed (`httpx.ReadError` — Ollama died mid-run) before writing JSON, so the **eviction-cost section never produced data**. Warm medians across the 5 configs were 18.69 / 18.84 / 19.47 / 18.81 / 19.22 s — non-monotonic in `num_ctx`, i.e. **no measurable effect**; `num_predict -1` vs `300` was a 0.4 s difference with identical generated-token counts. Separately the script's own fidelity check is **buggy**: `measure_llm_options.py:246` does `int(prompt_chars / 1.3)`, but English is ~4 chars/token, so it reported a bogus **+227 % prompt drift** that is really ≈ +6 %. |
| 9 | **Dangling "according to," in retry output** | Cosmetic answer-quality bug, unasserted. |
| 10 | **Stages 7, 8, 9 (frontend + hardening)** | Not started / partial. |

### Deviations from `project.md` / `workflow.md` — read this before trusting the spec

1. **`citation.relevance` is a sigmoid of a raw logit, not a 0–1 similarity.**
   §6 shows `"relevance": 0.91`. `citations.py:15-17` states `relevance = sigmoid(cross_encoder_logit)`
   because the cross-encoder emits an unbounded logit that renders badly as a score. The
   *field name and type match* §6; the *semantics do not*. A future prompt assuming
   `relevance` is cosine similarity will be wrong. (Arguably §6's own example value is
   consistent with a sigmoid of a large positive logit, so this may be intended — but it is
   undocumented in §6.)
2. **`matched_text` is nullable in the DB and in the model** (`citations.py` `text | None`),
   while §6 says it is *required* ("Without it, evidence mode can't work"). Practically the
   code guards this, but the schema permits the state §6 forbids.
3. **`document_chunks` stores no chunk text** — text lives only in the Qdrant payload, joined
   via `qdrant_point_id`. This *matches* §5 literally (which lists no content column) but
   will surprise anyone querying the table directly, as it did me.
4. **No authentication/identity endpoints.** `users` has exactly 1 row, auto-created on first
   chat (`default_user_email = dev@localhost`). Consistent with §2 single-tenant, but not
   specified anywhere in §6.
5. **`environment`/`app_name` config keys** exist beyond §15's five listed variables, as do
   `QDRANT_COLLECTION`/`STORAGE_DIR` (§15) which are absent from `.env` — see the
   `.env`/`.env.example` drift table in §2.
6. **Ollama is absent from `docker-compose.yml`** though §4's containerization row implies
   Docker for services. In practice Ollama runs on the host. `minio` is in compose but is
   both unpullable and unused (§2 puts uploads on local disk).
7. **`workflow.md` Golden Rule 2 vs. `backend/app/api/routes/`** — that package contains
   **four 0-byte empty files** (`chat.py`, `documents.py`, `search.py`, `__init__.py`) with no
   importers. Golden Rule 2 forbids scaffolding unused routes. Same for
   `app/ingestion/chunking.py` (0 bytes, shadowing the real `chunker.py`), and
   `app/rag/{retriever,embeddings,reranker}.py` which are superseded by
   `app/services/{retrieval,embeddings,reranker}.py` (the `services/` versions are the ones
   imported). **These are dead files and should be deleted, not left as-is.**
8. **`build/` is not running but is still defined** in compose for both backend and
   frontend, and `backend/Dockerfile` / `frontend/Dockerfile` exist. Whether the Dockerfiles
   still build is **unverified** this session.
9. **`requirements.txt` exists alongside `pyproject.toml`/`uv.lock`.** `uv` is the mandated
   package manager (§4); the presence of a second dependency manifest is a drift risk and
   `requirements.txt` was **not** verified to be in sync.

---

## 8. Git state

**Branch:** `main`, tracking `origin/main`, **ahead by 1** (unpushed).
**Remote:** `https://github.com/kartik00052/rag_policy_qa.git` (fetch + push).

### `git log --oneline -20` (10 commits total)

```
65adf88d fix(rag): grounding checks, content-attribution, streaming validate-then-replay
7528a965 docs: backend status snapshot 2026-09-27
0ce43b0d stage4-6: rerank + evidence gate, LangGraph generation, streaming chat API
1a6aab01 stage0-3: FastAPI backend, data layer, ingestion, hybrid retrieval
3ef58125 chore: stop tracking node_modules (WORKFLOW.md Golden Rule 6)
9fee7411 Replace README.md with project.md and add workflow.md
9ca42ed5 Update README to v5: concrete Espresso/Ivory color system and theming
111bd67c Fix .gitignore patterns that were silently broken
b0d4cbd6 Initialize README with project overview and specifications
c81a9d4c Initial commit
```

### `git status --short`

```
 M backend/app/api/debug.py
 M backend/scripts/verify_generation.py
?? backend/scripts/measure_llm_latency.py
?? backend/scripts/measure_llm_options.py
```

All four are **in-progress work from this session, not stray debug artifacts**:

| Path | Nature |
|---|---|
| `app/api/debug.py` (+89 lines) | The `POST /api/v1/_debug/ask` telemetry endpoint backing the latency work. **This is the only reason `openapi.json` lists that route** — it is not committed, so the route disappears at `HEAD`. |
| `scripts/verify_generation.py` | **BUG-C fix, applied and verified this session but NOT committed.** Adds `await_server()`: a pre-flight loop that blocks until `/health` returns `status=="ok"` **and every dependency is green**, exiting **2** (distinct from 1 = a generation check failed) after 180 s. Necessary because the server binds its port *before* finishing dependency checks, so it will happily return 200 with Ollama down — which would have turned 10 connection errors into a bogus "generation failure" verdict. Verified on all three paths: healthy → `None` in 0.2 s; `status:ok` + failed `ollama` dep → exit 2 with `unhealthy: ollama`; nothing listening → exit 2 with `ConnectError`. |
| `scripts/measure_llm_latency.py` | Latency measurement tool (untracked, from the deferred latency phase) |
| `scripts/measure_llm_options.py` | Ollama option sweep (untracked; crashed before writing JSON — see §7 item 8) |

### `node_modules` tracking cleanup — **COMMITTED, not pending**

`3ef58125 chore: stop tracking node_modules (WORKFLOW.md Golden Rule 6)` is in history, and
`git ls-files | Select-String node_modules` returns **0 matches**. `.gitignore` exists at
the repo root and covers `node_modules/`, `dist/`, `dist-ssr/`, `*.tsbuildinfo`, `.vite/`,
`.venv/`, `__pycache__/`, `*.py[cod]`, `.pytest_cache/`, `.ruff_cache/`, `.mypy_cache/`,
`backend/storage/`, `*.log`, `.env`, `.env.*` with `!.env.example`, `.DS_Store`,
`Thumbs.Db`. **No secret is tracked.**

**One `.gitignore` nit worth noting:** the app-data comment says "README section 2" but
`README.md` was replaced by `project.md` in `9fee7411` — stale comment, harmless.

---

## 9. Immediate next-step candidates

A plain checklist against `project.md` §16. No ordering, no recommendation.

**Backend correctness**
- [ ] Get FIX-1 (content attribution) actually working: the meal-allowance answer is correct but returns `citations=0`. The planned fallback if the contradiction retry lands on `GENERATION_REJECTED_ANSWER` is to tighten the retry shape instruction to a single sentence starting with Yes/No plus a citation marker.
- [ ] Run `uv run python -m scripts.verify_generation --runs 3` to completion and record the real per-run pass/fail table. No 3-run evidence exists for the current code; the only data is 6/10 questions of run 1.
- [ ] Re-run `uv run python -m scripts.verify_stage6.py` to re-establish the incremental-SSE and final-event-shape assertions against current code.
- [ ] Fix the dangling `"according to,"` produced by the contradiction retry.
- [ ] Decide whether to delete the 4 empty `app/api/routes/*` files + `app/ingestion/chunking.py` + the superseded `app/rag/{retriever,embeddings,reranker}.py` (Golden Rule 2 / dead code).

**Missing API surface**
- [ ] Build `GET /api/v1/documents/{id}/pages/{page}` (page text + bounding boxes). Required by §6, blocks §16 item 3.

**Ingestion coverage**
- [ ] Ingest an XLSX end to end and a CSV end to end (§2 requires all four formats; neither has ever been run).
- [ ] Investigate/clean up the stale `documents` row with `status='failed'` for the docx.

**Latency (deferred, explicitly)**
- [ ] Re-run the Ollama option sweep to completion so the `keep_alive` eviction-cost question has data; fix the chars→tokens bug at `measure_llm_options.py:246` (`/ 1.3` should be ~`/ 4.0`) so the fidelity check stops reporting bogus +227% drift.
- [ ] Model benchmark: qwen2.5:1.5b vs qwen2.5:3b vs llama3.2:3b vs phi3.5. **Requires pulling `llama3.2:3b` and `phi3.5` first** — neither is local.

**Housekeeping**
- [ ] Commit or discard the 4 uncommitted/untracked files in §8 (notably `verify_generation.py`, which contains a verified BUG-C fix).
- [ ] Push `65adf88d` (1 commit unpushed).
- [ ] Reconcile `.env` / `.env.example` key drift (§2), especially adding `OLLAMA_MODEL`.
- [ ] Reconcile `requirements.txt` with `pyproject.toml`/`uv.lock`, or delete it.
- [ ] Fix or remove the `minio` compose service (unpullable image, unused by design).
- [ ] Investigate the stray `pgadmin` container and the second `postgres:18` on 5433, neither of which is in `docker-compose.yml`.

**Frontend (not started)**
- [ ] Stage 7: app shell, Zustand stores (`workspaceStore`, `conversationStore`, `evidenceStore`, `themeStore`), chat thread streaming from the real backend, clickable citation chips, Evidence panel with real `matched_text` highlights.
- [ ] Stage 8: Espresso/Ivory palettes, Fraunces/Lora + Geist pairing, all §11 states.
- [ ] Stage 9: UI error states; golden-rule re-check.

**Blocked / dependent**
- [ ] §16 item 3 (click a citation → see the highlighted source passage) cannot be completed until the `pages/{page}` endpoint exists.
- [ ] §16 items 5 and 6 (theme switch, non-generic look) are frontend-only and unblocked.

---

*Report generated read-only. No pipeline code, no `project.md`/`workflow.md`, and no
database schema was modified. The only state changes made while gathering facts were the
transient upload-and-delete performed by `scripts/verify_injection.py`, which self-cleans
(confirmed: counts returned to 2 documents / 13 chunks / 13 Qdrant points), and the
starting of Docker, the compose services, Ollama and the backend server, none of which
alter the repository.*

---

## 9. Current gap register and implementation plan

This section is the active work list for completing V1. It supplements the verified
status above; it does not replace `project.md` or `workflow.md`. The order is deliberate:
backend correctness must be proven before the frontend is wired to it, and the evidence
viewer must consume the real citation contract rather than mock data.

### 9.1 Scope guardrails

The following decisions remain fixed while closing the gaps:

- Keep the V1 architecture: React/Vite → FastAPI → PostgreSQL/Qdrant/local disk.
- Keep ingestion inline for V1. Do not introduce Celery, a worker service, MinIO/S3,
  Kubernetes, microservices, or a new vector database.
- Keep the existing RAG sequence:
  `retrieve_hybrid → rerank → check_evidence → generate_answer → cite`.
- Keep retrieved text as untrusted data and preserve the no-answer-without-evidence rule.
- Do not build policy comparison, admin dashboard, query decomposition, multi-tenant
  permissions, SSO/OIDC, or other `(V2)` features.
- Authentication and per-document authorization are not V1 features. Until they are
  explicitly added to `project.md`, only apply development hardening such as disabling
  debug routes in non-development environments and avoiding sensitive error details.
- Do not change the V1 database schema to compensate for frontend gaps. Use the existing
  `matched_text`, page, section, document, and citation fields.

### 9.2 Backend gaps — required V1 work

| ID | Gap | Files/area | Required result | Priority |
|---|---|---|---|---|
| BE-01 | Content attribution is still failing | `app/rag/graph.py`, citation/grounding code, `scripts/verify_generation.py` | The meal-allowance generation gate passes with a non-empty citation set and correct `matched_text`; complete the configured multi-run verification, not just one successful run. | P0 |
| BE-02 | Evidence gate misses table questions | `app/services/evidence.py`, retrieval/reranking tests | Preserve the refusal rule while making table evidence retrievable for questions such as the New York accommodation-cap query. Add a regression test for the known false negative. | P0 |
| BE-03 | Known topical false positives need a guarded outcome | evidence/generation validation | The part-time sick-leave question must not receive a full-time policy answer. Verify the no-extrapolation/grounding refusal path with a regression test. Do not solve this by lowering the evidence threshold blindly. | P0 |
| BE-04 | XLSX and CSV have no end-to-end proof | `app/ingestion/parser.py`, chunker, ingestion verification scripts | Ingest real XLSX and CSV fixtures, verify structured/table chunks and metadata in Qdrant/PostgreSQL, and verify ready status. | P0 |
| BE-05 | Evidence viewer endpoint is missing | `app/api/documents.py`, document schemas/services | Implement `GET /api/v1/documents/{id}/pages/{page}` according to `project.md` §6. It must return the source context and support the citation `matched_text` highlight without inventing a new storage model. | P0 |
| BE-06 | Streaming contract needs final verification | `app/api/chat.py`, `app/rag/graph.py`, `scripts/verify_stage6.py` | Re-run the Stage 6 harness and prove token events, final citations, refusal events, persistence, and replay timing. Preserve validate-then-replay so rejected model output is never streamed. | P1 |
| BE-07 | Ingestion failure state is difficult to diagnose | document ingestion/status handling | Preserve the current schema, but ensure failures are logged with document ID and exception context, cleanup is attempted, and the document cannot be retrieved unless it is ready. | P1 |
| BE-08 | Retrieval readiness filtering must be explicit | `app/services/retrieval.py` | Ensure only `documents.status=ready` documents are eligible for retrieval. Add a test for uploading/failed documents. | P1 |
| BE-09 | Stale failed fixture row remains | database/test fixture cleanup | Remove or explain the stale failed DOCX row only through the existing cleanup process. Do not hide failures by deleting them during normal ingestion. | P1 |
| BE-10 | Citation contract has documented/schema drift | citation schema/model/migration/docs | Decide within the existing V1 contract whether `matched_text` is required at persistence time and document that `relevance` is a sigmoid of the cross-encoder logit. Avoid silently changing its meaning. | P1 |
| BE-11 | Cosmetic retry output defect | generation/retry prompt formatting | Remove the dangling `according to,` output and add a focused generation-format test. | P2 |
| BE-12 | LLM measurement scripts are incomplete | `scripts/measure_llm_options.py`, latency scripts | Fix the chars-to-token estimate, rerun only if model comparison is needed for V1, and label benchmark results as diagnostic rather than product functionality. | P2 |

### 9.3 Backend quality and repository hygiene

Complete these after the P0 behavior is stable and before declaring backend V1 complete:

- Run `uv run pytest -q` and retain the current passing baseline of 106 tests as a
  regression checkpoint.
- Add endpoint-level tests for upload, document list/detail, page context, chat SSE, and
  refusal responses. Keep external-service tests explicit and runnable against the local
  Compose dependencies.
- Re-run the relevant verification script after each backend change; do not rely solely on
  unit tests for Qdrant, Docling, Ollama, or SSE behavior.
- Remove the empty/superseded route and ingestion files identified in §7 only after
  confirming they have no imports or references. Do not create replacement scaffolding.
- Resolve the uncommitted files listed in §8, reconcile `requirements.txt` with the
  mandated `pyproject.toml`/`uv.lock` workflow, and reconcile `.env` with `.env.example`.
- Remove or correct the unused/unpullable MinIO Compose service. Keep local disk storage,
  because that is the V1 decision.
- Add Compose health checks/readiness ordering and keep infrastructure ports development-
  only. Do not add new production services.
- Keep backend runtime without auto-reload for ingestion verification. Do not change the
  selector event-loop setup required on Windows.

### 9.4 Frontend gaps — Stage 7

The frontend must be connected to real backend contracts; it must not use mock answers or
placeholder citation data.

| ID | Gap | Required implementation | Done when |
|---|---|---|---|
| FE-01 | Application shell is not wired | Implement the app layout and route/page composition using the existing React/Vite/shadcn stack. | App boots and renders the specified workspace without placeholder-only content. |
| FE-02 | Stores are incomplete | Add and wire `workspaceStore`, `conversationStore`, `evidenceStore`, and `themeStore` according to `project.md` §8. | Workspace, conversation, evidence, and theme state have one clear owner each. |
| FE-03 | API client is incomplete | Implement typed document, conversation, chat, page-context, and health calls in `frontend/src/lib/api.ts`. | All calls use the real V1 endpoints and expose typed loading/error states. |
| FE-04 | SSE client is missing | Implement `frontend/src/lib/sse.ts` and `useChatStream` for token, citation, done, and error events. | Partial assistant text renders during the stream; the final event attaches citations. |
| FE-05 | Chat UI is missing | Complete `pages/Chat.tsx` and chat components using the actual conversation contract. | User can create/select a conversation, submit a question, see streaming output, and retry a failure. |
| FE-06 | Documents UI is missing | Complete `pages/Documents.tsx` and `useUploadDocument`. | User can upload PDF/DOCX/XLSX/CSV, see status transitions, and open a ready document. |
| FE-07 | Citation chips are missing | Add citation rendering and connect every citation click to `evidenceStore.openCitation`. | Citations show source metadata and open the third pane. |
| FE-08 | Evidence viewer is missing | Add evidence components that call the page-context endpoint and highlight `matched_text`. | Clicking a citation shows the correct document page/section and highlighted passage. |
| FE-09 | Theme system is missing | Implement the specified Espresso/Ivory CSS tokens and `data-theme` persistence. | Light/dark themes match `project.md` §10 and survive reload. |
| FE-10 | Empty/loading/error states are missing | Design states for no documents, processing, failed upload, no evidence, stream failure, and unavailable backend. | No state is represented only by a generic spinner or “No data” label. |

### 9.5 Frontend Stage 8 and Stage 9

After FE-01 through FE-10 work:

1. Apply the specified typography, warm accent palette, density, spacing, and functional
   motion from `project.md` §§9–11. Do not introduce a generic SaaS dashboard or default
   blue/purple AI styling.
2. Check responsive behavior for the three-pane workspace, including the mobile behavior
   specified by the project UI section.
3. Verify keyboard focus, citation activation, readable contrast in both themes, upload
   progress, stream interruption, and evidence-panel close behavior.
4. Build the frontend for production and verify the built application against a running
   backend. The Dockerfile must not be treated as complete while it only starts a Vite
   development server.

### 9.6 Recommended execution sequence

Use this sequence to avoid rework:

1. **BE-01 to BE-04:** stabilize attribution, table evidence, false-positive refusal, and
   all four supported file formats.
2. **BE-05 to BE-08:** complete page evidence retrieval, verify SSE, and enforce ready-only
   retrieval/error visibility.
3. **Backend validation:** run unit tests, endpoint tests, Stage 2/3/4/5/6 verification,
   and a clean working-tree review.
4. **FE-01 to FE-04:** establish the application shell, stores, typed API client, and SSE
   stream against the already-proven backend.
5. **FE-05 to FE-08:** implement chat, documents, citation chips, and the evidence viewer.
6. **FE-09 to FE-10:** implement themes and all operational states.
7. **Stage 8/9 validation:** verify the rendered three-pane UI in both themes, run a real
   upload-to-citation flow, and build the frontend for deployment.
8. **Housekeeping:** resolve stale/dead files, Compose drift, dependency-manifest drift,
   and the remaining uncommitted work only after behavior is verified.

### 9.7 V1 Definition of Done checklist

- [ ] PDF, DOCX, XLSX, and CSV each complete upload → parse → chunk → embed → index → ready.
- [ ] Hybrid retrieval, reranking, and evidence refusal are covered by tests and live checks.
- [ ] Known table questions retrieve table evidence without weakening refusal behavior.
- [ ] Generated answers contain valid citations with page, section, and matched text.
- [ ] Unsupported questions refuse instead of guessing; known topical false positives are
	  covered by regression tests.
- [ ] Page-context endpoint returns the evidence needed by the viewer.
- [ ] SSE token/final/error events and persistence are verified by the Stage 6 harness.
- [ ] Frontend supports upload, document status, chat streaming, citation clicks, and the
	  highlighted evidence panel using real API responses.
- [ ] Light and dark themes implement the specified Espresso/Ivory design.
- [ ] Loading, empty, processing, failed, insufficient-evidence, and backend-unavailable
	  states are usable and visible.
- [ ] Backend tests and relevant live verification scripts pass; frontend production build
	  succeeds; no V2 feature or unapproved service was introduced.

### 9.8 Working rule for future updates

When a gap is closed, update its row and the corresponding Build Order stage with:

1. the exact file or endpoint changed;
2. the command/test/live request used for verification;
3. the observed result; and
4. any remaining limitation.

Do not mark a gap complete because code exists. Mark it complete only after the behavior
is exercised through the relevant boundary and the result is recorded here.

---

## 10. Master backend implementation prompt

The following prompt is the recommended instruction for an implementation agent working on
the remaining backend V1 work. It is intentionally comprehensive so that implementation
does not restart discovery, change the architecture, duplicate services, or skip live
verification.

### Copy/paste prompt

```text
You are implementing the backend of the RAG Policy Assistant in this repository.

Your objective is to complete and harden the existing V1 backend, not to redesign the
project. Read these files completely before editing anything:

1. project.md — the authoritative product scope, architecture, API contract, database
   schema, RAG flow, and UI/backend contracts.
2. workflow.md — the mandatory engineering workflow and build-order rules.
3. backend/STATUS.md — the verified implementation state, known defects, exact gaps, and
   this execution plan.

The existing architecture is fixed:

React/Vite frontend → FastAPI backend → PostgreSQL + Qdrant + local disk

The existing backend flow is fixed:

upload → Docling parse → section/heading chunking → dense and BM25 representations →
Qdrant/PostgreSQL persistence → hybrid retrieval → RRF fusion → cross-encoder reranking →
evidence gate → LangGraph generation → citation validation → validate-then-replay SSE.

Do not replace or bypass this flow.

## Non-negotiable constraints

- Implement V1 only. Do not build any feature under a V2 heading.
- Do not add Celery, a worker service, MinIO/S3, Kubernetes, microservices, a new vector
  database, a new ORM, or an unapproved dependency.
- Keep ingestion inline for V1, as specified by project.md.
- Preserve the no-answer-without-evidence rule. The LLM must never answer from general
  knowledge when retrieved policy evidence is insufficient.
- Treat retrieved document text as untrusted data, never as instructions.
- Preserve the existing Windows selector event-loop configuration. Do not run the backend
  with auto-reload during ingestion verification.
- Do not modify frontend files. Do not implement frontend work as a substitute for missing
  backend contracts.
- Do not change the database schema unless project.md already requires it. In particular,
  do not add authentication, permissions, document versions, audit logs, or multi-tenant
  features because they are outside V1.
- Do not silently swallow exceptions. Errors must be visible in logs and represented by a
  stable API error contract without leaking internal paths, stack traces, credentials, or
  provider internals to clients.
- Do not delete failed records merely to make a test pass. Fix the failure or use an
  explicit cleanup script.
- Use the existing package manager and lockfile: uv, pyproject.toml, and uv.lock.
- Make minimal, focused changes. Do not refactor unrelated working code.

## Required backend outcomes

Complete all of the following:

### Phase 0 — Baseline and repository safety

1. Inspect git status before editing and identify existing user changes. Do not overwrite
   unrelated work.
2. Confirm the backend environment with uv and verify the current test baseline.
3. Confirm the running dependency procedure from backend/scripts/start_server.ps1 and do not
   start the server with a direct uvicorn command on Windows.
4. Map every change to a gap ID in backend/STATUS.md. If a requirement conflicts with
   project.md, stop and report the conflict instead of guessing.

### Phase 1 — Fix generation attribution and grounding

Fix BE-01, BE-02, and BE-03 without weakening evidence protection.

1. Reproduce the current meal-allowance citation failure using the existing verification
   script and inspect the complete retrieval, reranking, evidence, prompt, parser, and
   citation path.
2. Make the answer-to-source attribution reliable. A valid answer must contain citations
   that point to the supporting document, page, section, and matched_text.
3. Ensure citations are emitted for valid answers and are persisted consistently with the
   assistant message.
4. Ensure table questions, including the New York nightly accommodation-cap question,
   can retrieve and use the markdown table evidence already stored in Qdrant.
5. Preserve the refusal path for insufficient evidence.
6. Ensure the part-time sick-leave question cannot be answered from a full-time-only
   passage. Add or improve grounding/no-extrapolation validation rather than blindly
   lowering the evidence threshold.
7. Preserve validate-then-replay behavior: rejected or unvalidated model output must not
   be streamed to the client.
8. Add focused regression tests for citation presence, matched_text correctness, table
   evidence, insufficient evidence, and the known topical false positive.

### Phase 2 — Complete document ingestion coverage

Fix BE-04 and verify the existing ingestion contract.

1. Use real XLSX and CSV fixtures compatible with the current Docling parser.
2. Verify each format through upload, parse, chunk, embedding, Qdrant indexing,
   PostgreSQL metadata persistence, and ready status.
3. Confirm headings, page metadata where available, section metadata, chunk indexes, and
   markdown table preservation.
4. Ensure malformed or unsupported input produces a visible failed status and useful
   server-side diagnostic context.
5. Keep local disk storage and the current document schema.
6. Add or update tests/scripts so PDF, DOCX, XLSX, and CSV coverage is repeatable.

### Phase 3 — Complete evidence retrieval API

Fix BE-05.

1. Implement the existing V1 contract for:
   GET /api/v1/documents/{id}/pages/{page}
2. Read the exact response contract from project.md before coding. Do not invent a new
   endpoint shape or new persistence model.
3. Return the source context needed by the evidence viewer, including document identity,
   page/section information, source text, and enough information for matched_text
   highlighting.
4. Validate document and page identifiers and return stable not-found responses.
5. Ensure the endpoint does not expose files or paths outside the configured storage area.
6. Add endpoint tests for valid page context, missing document, missing page, and invalid
   identifiers.

### Phase 4 — Enforce retrieval and ingestion correctness

Fix BE-07, BE-08, and BE-09.

1. Make retrieval explicitly eligible only for documents with status=ready.
2. Verify that processing and failed documents cannot contribute chunks to answers.
3. Preserve document status transitions and cleanup behavior on ingestion failures.
4. Log document ID, stage, exception type, and safe diagnostic context when ingestion fails.
5. Do not expose raw exception strings through public SSE or HTTP responses.
6. Investigate the stale failed DOCX record using the existing cleanup/verification tools;
   remove it only through explicit cleanup after its cause is understood or documented.
7. Add regression tests for failed/processing documents and cleanup behavior.

### Phase 5 — Stabilize streaming and API contracts

Fix BE-06 and BE-10.

1. Re-run scripts/verify_stage6.py against the running backend.
2. Verify token events, final event shape, citations, refusal responses, persistence, and
   replay timing.
3. Preserve the intentional validate-then-replay design so unsafe or invalid output never
   reaches the client.
4. Ensure disconnects and provider failures clean up tasks and do not leave incomplete
   assistant records falsely marked as successful.
5. Document the exact meaning of citation relevance: it is the sigmoid of the raw
   cross-encoder logit, not cosine similarity.
6. Enforce the citation persistence contract for matched_text consistently with the
   existing V1 evidence requirements. Do not silently fabricate matched text.
7. Add or update SSE contract tests for success, refusal, validation retry, provider
   failure, and client disconnect behavior.

### Phase 6 — Backend hardening and operational cleanup

Complete BE-11, BE-12, and the backend items in §9.3.

1. Remove the dangling “according to,” retry-output defect and add a focused regression
   test.
2. Fix the chars-to-token estimate in the measurement script only if that script remains
   part of the repository workflow. Clearly label benchmark output as diagnostic.
3. Reconcile .env and .env.example without committing secrets.
4. Reconcile requirements.txt with pyproject.toml/uv.lock, or remove the redundant manifest
   according to the repository’s mandated uv workflow.
5. Remove or correct the unused/unpullable MinIO Compose service; keep local storage as
   required by V1.
6. Add or correct Compose health checks and readiness ordering without adding services.
7. Review debug endpoints. They may remain available for development verification only, but
   must not be exposed in non-development environments and must not return sensitive
   provider, path, traceback, or configuration details.
8. Confirm no dead duplicate backend modules are imported. Remove empty or superseded
   backend files only after checking all references.
9. Resolve or clearly record all uncommitted backend work before declaring completion.

## Required validation sequence

After each phase:

1. Run targeted static checks and tests for the changed files.
2. Start the backend through backend/scripts/start_server.ps1.
3. Exercise the affected endpoint or verification script against the real local services.
4. Check PostgreSQL and Qdrant consistency where ingestion or deletion changed.
5. Inspect logs for hidden exceptions, warnings, leaked secrets, or swallowed failures.
6. Update the matching gap row in backend/STATUS.md with the exact command and observed
   result. Do not mark a gap complete based only on code inspection.

Before final completion, run all of the following where applicable:

- uv run pytest -q
- scripts/test_metadata_checks.py
- scripts/verify_stage2.py
- scripts/verify_stage3.py
- scripts/calibrate_evidence.py or the documented evidence regression test
- scripts/verify_generation.py with the configured multi-run option, if available
- scripts/verify_injection.py
- scripts/verify_stage6.py
- a real PDF, DOCX, XLSX, and CSV upload lifecycle
- a real supported question with citations
- a real unsupported question that correctly refuses
- a real page-context request used to retrieve highlighted evidence

If a verification script is unavailable, broken, or depends on a missing model, record the
exact blocker and do not claim the related gap is complete.

## Completion report requirements

When all feasible backend V1 work is complete, provide:

1. A concise list of files changed and why.
2. A gap-by-gap status for BE-01 through BE-12.
3. Exact test and live verification commands with results.
4. Database/Qdrant consistency results after ingestion and cleanup.
5. Any remaining known limitation, false positive/negative, or environment dependency.
6. Confirmation that frontend files and V2 scope were not changed.
7. An updated backend/STATUS.md that records evidence instead of assumptions.

Never claim the backend is complete if citation attribution, table evidence, all four file
formats, page evidence retrieval, SSE validation, or refusal behavior remains unverified.
```

---

## 11. Reviewed session handoff prompt and audit addendum

The prompt supplied in the preceding session handoff is useful and has been incorporated
below as an execution addendum. It must be read together with Sections 1–10 of this file,
`project.md`, and `workflow.md`. The repository status recorded in this file is newer than
the original handoff snapshot, so the current working tree, tests, and commit history must
always be checked before executing any command from the handoff.

### 11.1 Reconciliation decisions

- The original handoff reports **95 tests**, while the latest verified status in this file
  reports **106 passing tests**. Treat the test count as a lower-bound regression rule:
  preserve the current passing baseline and require all newly added tests to pass. Never
  restore an old numeric target by removing tests.
- The original handoff Git log and working-tree list are historical. Run `git status --short`
  and `git log --oneline -10` first. Do not commit, discard, reset, or overwrite changes
  until the current state is confirmed.
- The original FIX-1 through FIX-4 descriptions are valuable regression context. Verify
  their current implementation rather than assuming the older uncommitted state still
  exists.
- The original FIX-5 cleanup recommendation is valid only after reference search confirms
  the files are unused. Delete dead files; do not add placeholder modules or redirect
  comments that violate the workflow rule against unused scaffolding.
- The pages endpoint contract is part of V1 and must use the existing PostgreSQL metadata
  and Qdrant payload arrangement. A database index migration is appropriate, but do not
  add a chunk-content column or a new storage system.
- The Dockerfile instruction is conditional: first inspect the existing Docker and uv
  workflow, then make the smallest change that produces a valid image. Do not replace the
  mandated local development workflow with an unapproved dependency manager.
- BUG-J is an investigation item, not a guaranteed implementation recipe. Confirm how the
  installed Docling/openpyxl versions behave with password-protected workbooks before
  selecting a timeout, exception type, or status transition.

### 11.2 Enhanced execution handoff

```text
Continue backend V1 implementation from backend/STATUS.md. Treat project.md as the
authoritative product and architecture specification and workflow.md as the mandatory
engineering process. Treat the current repository state as authoritative over any older
session snapshot.

Before editing:

1. Read project.md, workflow.md, and backend/STATUS.md completely.
2. Run git status --short and git log --oneline -10. Preserve unrelated user changes.
3. Run uv run pytest -q and record the actual baseline; the currently documented baseline
   is 106 passing tests, not 95.
4. Confirm backend/.env has RELOAD=false and start the server only with:
   powershell -ExecutionPolicy Bypass -File backend\scripts\start_server.ps1
5. Verify /health and confirm PostgreSQL, Qdrant, Redis, and the configured LLM provider
   are available before running live verification.
6. Check whether the FIX-1/FIX-2/FIX-3 working-tree changes from the older handoff are
   already committed, still uncommitted, or absent. Do not reapply or commit them blindly.

Execute the remaining work in this exact order:

PHASE 0 — State reconciliation and safe commit

- Reconcile the actual working tree with the FIX-1 through FIX-5 descriptions.
- Verify imports for grounding and citation modules before any commit.
- Run focused grounding, evidence, citation, and graph tests.
- Commit only if the repository's current changes are understood and the user changes are
  intended. Use a descriptive commit body that names the verified fixes and test result.
- Do not require a clean tree if unrelated user work exists; isolate the implementation
  changes instead.

PHASE 1 — End-to-end generation verification

- Reproduce the meal-allowance and business-class scenarios.
- Ensure valid answers have citations and matched_text.
- Ensure the business-class answer contains the restrictive economy rule.
- Ensure the meal allowance answer contains the correct amount.
- Verify that validate-then-replay keeps rejected tokens away from the client.
- Add a server-readiness retry to verify_generation.py only if the current script still
  has the race. The retry must be async, bounded, dependency-aware where appropriate, and
  must return a distinct failure status. Do not duplicate nested AsyncClient lifetimes or
  hide a failed health check.
- Run the configured multi-run generation gate and record every failure reason.

PHASE 2 — Evidence pages endpoint

- Implement the existing V1 endpoint:
  GET /api/v1/documents/{document_id}/pages/{page_number}
- Follow the exact response contract in project.md. The expected chunk fields are
  chunk_index, section, content, and content_type unless the authoritative contract says
  otherwise.
- Query document_chunks by document_id and page_number, then retrieve content and content
  type from Qdrant using qdrant_point_id.
- Return stable 404 responses for an unknown document, a page with no chunks, and a
  document type that does not carry page numbers. Do not expose filesystem paths.
- Add the composite document_id/page_number index through a new Alembic migration only
  after confirming the model and migration conventions.
- Add unit and endpoint tests for success, invalid UUID, missing document, missing page,
  null-page documents, Qdrant lookup failure, and chunk ordering.

PHASE 3 — Targeted backend test coverage

Add only tests that match the installed code contracts and existing test style:

- test_evidence.py: evidence threshold, minimum chunk count, table evidence, and refusal.
- test_judge_answer.py: empty answer, numbered restatement, high overlap, clean cited
  answer, contradiction, and the intentional below-threshold citation behavior.
- test_chunker.py: section/table preservation, long-section splitting, short-run merging,
  no duplicated table text, and contiguous chunk indexes.
- test_reranker_passage.py: heading_path inclusion and content-only fallback without model
  loading.

Do not assert implementation details that are not part of the public or documented
contract. Run the focused tests, then the complete suite. The suite must not regress from
the actual baseline established in Phase 0.

PHASE 4 — XLSX and CSV end-to-end verification

- Create deterministic, small fixtures in the repository's established fixture location.
- Include an XLSX with two sheets and a policy table, and a CSV with expense-code rows.
- Use the real upload endpoint and poll the documented status until ready or failed.
- Verify PostgreSQL metadata, Qdrant payloads, chunk metadata, table representation, and
  citations from a real question.
- Keep evidence_min_score at its calibrated value. Do not lower it to hide the known table
  false negative.
- Clean all temporary rows, vectors, and files after the verification run.

PHASE 5 — Audited bug fixes and housekeeping

Investigate and fix only confirmed defects:

- BUG-A: verify that _replay uses asyncio correctly and add a focused import/execution
  test if the issue exists.
- BUG-B: add a regression test proving below-threshold citations intentionally do not
  enter contradiction correction when that is the current contract.
- BUG-C: add the bounded server readiness check only if still missing.
- BUG-D: verify the grounding import is tracked and importable.
- BUG-E: broaden numbered-restatement detection only if it does not reject legitimate
  answers; add a regression test.
- BUG-F: log the purge count from interrupted-document reconciliation.
- BUG-G: enforce citation_excerpt_max_chars while preserving useful matched text and add a
  boundary test.
- BUG-H: add and apply the page query index migration after inspecting conventions.
- BUG-I: handle client disconnect during replay without masking unrelated generation
  failures; log at the appropriate warning level.
- BUG-J: reproduce password-protected workbook behavior with the installed parser stack,
  enforce a bounded parse operation, and map the confirmed parser failure to a visible
  failed document status. Do not depend on a guessed exception class.

For empty modules, duplicate route packages, requirements.txt, Dockerfiles, Compose
services, and environment-key drift: inspect references first, make the smallest
project-aligned cleanup, and verify imports, builds, and startup afterward. Do not change
the V1 architecture or add frontend work.

Final gate:

- uv run pytest -q passes from the current baseline.
- The backend starts with start_server.ps1 and /health is healthy.
- PDF, DOCX, XLSX, and CSV ingestion are verified or any environment blocker is recorded.
- Generation, citation attribution, grounding refusal, page retrieval, and SSE behavior
  are verified with live or targeted tests.
- PostgreSQL and Qdrant counts are consistent after test cleanup.
- No secrets are committed, no V2 feature is added, and no frontend file is changed.
- Update the matching BE gap rows in this file with commands, results, and limitations.
```

### 11.3 Handoff quality rules

The supplied handoff must not be interpreted as permission to make unverified changes. In
particular:

- Do not lower `evidence_min_score` below `0.0` to make one table query pass.
- Do not stream tokens before validation clears them.
- Do not commit `grounding.py` without its regression tests.
- Do not use a stale test count as the project baseline.
- Do not force a clean Git tree by deleting user work.
- Do not mark a phase complete without the specified boundary verification and a recorded
  result in this status file.
