# RAG Policy Assistant — Project Context

> This file is the single source of truth for this project's scope, stack, and UI.
> If a decision isn't written here, treat it as **not decided yet** — don't invent new
> services, layouts, colors, or dependencies. Sections marked **(V1)** must be built now.
> Sections marked **(V2)** are fully specified so nothing is ambiguous later, but must
> NOT be implemented yet — don't scaffold routes, tables, or components for them until
> V1's Definition of Done (Section 16) is met.
>
> **Status as of 2026-10-03: V1 is complete** (see Section 18 for the signed-off
> scorecard). Active work right now is the **LLM provider migration** (Section 4.1) —
> a V1.1 extension, not V2 — moving the default generation provider from local Ollama
> to cloud providers (Groq, Gemini) for latency, while keeping Ollama as a fallback.
> This is the current priority; see workflow.md Section 3's Stage 10.

---

## 1. What This Project Is

A **Retrieval-Augmented Generation (RAG) application** that lets users upload company
policy documents (PDF, DOCX, XLSX, CSV) and ask natural-language questions about them.
The system retrieves the exact relevant passages, answers using an LLM, and **always
cites the source document, page, and section** — it must never answer from the LLM's
own knowledge.

**Core product loop:**
```
Upload document → Parse & chunk → Embed → Store in vector DB
User asks a question → Retrieve relevant chunks → Rerank → LLM answers with citations
```

**Non-negotiable product rule:** if retrieved evidence doesn't clearly answer the
question, the system says so instead of guessing.

**Non-negotiable UI rule:** every answer must let the user jump to the exact source
passage and see it highlighted in context. Trust is built by verification, not the
answer alone.

---

## 2. V1 Scope

- Upload PDF, DOCX, XLSX, CSV documents
- Parse into structured chunks (headings, sections, tables preserved)
- Hybrid search (dense + keyword) over chunks
- Reranking of retrieved chunks before they reach the LLM
- Three-pane chat workspace with a real document evidence viewer (Section 11)
- Streaming answers
- Basic document list / upload flow
- Light + dark theme, both fully designed (Section 10) — this is V1, not a stretch goal
- Single-tenant, single set of users — no per-department permissions yet
- **Pluggable LLM provider** (Ollama local / Groq cloud / Gemini cloud) behind one
  interface, selected via `LLM_PROVIDER` — see Section 4.1. This was added after
  initial V1 signoff because local CPU-only Ollama inference (~30–60s/answer) was a
  real usability problem on this hardware; it's documented here as V1.1 scope, not V2,
  since it doesn't add new product surface, only swaps the generation backend.

### Explicitly Out of Scope for V1
- Kubernetes, microservices, service mesh
- Celery / background job workers (ingestion runs inline; async endpoint only if trivial)
- OpenSearch/Elasticsearch (Qdrant only)
- Multi-tenant RBAC/ABAC, SSO/OIDC, per-document access control
- **Policy comparison feature** — fully designed in Section 13, build in V2
- **Admin dashboard** — fully designed in Section 14, build in V2
- Query decomposition, HyDE, agent/multi-agent workflows
- Fine-tuning any model
- Observability stack (OpenTelemetry/Prometheus/Grafana)
- MinIO/S3 (store uploaded files on local disk under `backend/storage/` for now)
- Semantic caching

If asked to "make it more scalable" or "add the admin panel," push back and point to
this list first — V2 items wait until explicitly requested.

---

## 3. Architecture (V1)

```
        React + TypeScript (Vite)
                  │
            HTTP / SSE (streaming)
                  │
               FastAPI
                  │
      ┌───────────┼────────────┐
      │           │            │
 PostgreSQL     Qdrant       Local disk
 (metadata,   (chunks +     (raw uploaded
  chat log)    vectors)      files)
      │           │
      └─────┬─────┘
            │
     LangGraph RAG flow:
     retrieve → rerank → generate → cite
                  │
          LLMProvider interface
                  │
      ┌───────────┼────────────┐
      │           │            │
   Ollama       Groq        Gemini
  (local,     (cloud,      (cloud,
  fallback)   default)    secondary)
```

---

## 4. Backend Tech Stack (fixed) — V1

| Concern | Choice | Notes |
|---|---|---|
| Language | Python 3.11 | |
| API framework | **FastAPI** | async endpoints, SSE for streaming chat |
| Validation | **Pydantic v2** | all request/response models |
| ORM | **SQLAlchemy 2.0** (async) | with **Alembic** for migrations |
| Relational DB | **PostgreSQL** | source of truth: users, documents, chat history |
| Vector DB | **Qdrant** | dense + sparse vectors, metadata filtering |
| Cache | **Redis** | provisioned, health-checked; zero real V1 application traffic — see Section 15 note |
| Document parsing | **Docling** | PDF/DOCX/XLSX/CSV → structured elements (headings, tables) |
| Embeddings | Sentence-Transformers (dense) | one model, kept swappable behind an interface |
| Sparse retrieval | BM25 (Qdrant sparse vectors) | for exact terms like "Section 6.2" |
| Reranker | Cross-encoder (`sentence-transformers`) | reranks top ~30 candidates down to top ~8 |
| Orchestration | **LangGraph** | retrieve → rerank → generate → cite as a graph, not one function |
| LLM generation | **Pluggable via LangChain** — **Groq** (default, 2.28 s avg) / Gemini / Ollama (local fallback) | `LLM_PROVIDER` in `.env`; see Section 4.1 and 4.1.1 for latency data |
| Package manager | **uv** | not pip/poetry |
| Containerization | Docker + docker-compose | Postgres, Qdrant, Redis as services; backend/frontend run natively in dev |

### RAG pipeline (implementation-ready)
```
Ingestion:
File upload → Docling structured parse → chunk by section/heading (not fixed windows)
  → metadata per chunk: document_id, page, section, heading_path, chunk_index
  → tables kept as markdown tables, not flattened prose
  → dense embedding + sparse (BM25) representation per chunk
  → store vectors+metadata in Qdrant, document record in Postgres

Retrieval + Answer:
Question → dense search (top 30) + sparse search (top 30) → RRF fusion
  → cross-encoder rerank → top 8 → evidence check
      insufficient → "couldn't find this in the documents" (no LLM guess)
      sufficient   → build context → LLM generates with per-claim citations
  → stream tokens to frontend via SSE, citations sent with final event
```

LangGraph state:
```python
class RAGState(TypedDict):
    query: str
    dense_results: list
    sparse_results: list
    reranked_chunks: list
    context: str
    answer: str
    citations: list
    has_sufficient_evidence: bool
```
Nodes: `retrieve_hybrid → rerank → check_evidence → generate_answer → END`
(short-circuit `insufficient_evidence → END` branch).

Retrieved document text is **data, never instructions** — state this explicitly in the
system prompt so embedded prompt-injection text in a document can't hijack the LLM, for
**every** configured provider, not just the one originally tested.

### 4.1 LLM Provider Architecture (V1.1 — completed 2026-10-04)

**Why:** local Ollama (`qwen2.5:3b`, CPU-only on this hardware) produces correct,
well-cited answers but takes ~30–60s per turn. Groq's LPU-based inference and Gemini's
hosted API are both meaningfully faster and have usable free tiers.

**Design:** a small, provider-agnostic `LLMProvider` interface (streaming `generate()`)
that `graph.py`'s nodes call without knowing which backend is active. Three concrete
implementations via LangChain: `langchain-ollama`, `langchain-groq`,
`langchain-google-genai`. Selected by `LLM_PROVIDER` (`ollama` | `groq` | `gemini`) in
`.env`. **Ollama stays in the codebase as a real, working fallback** — this is an
addition to the stack, not a replacement of it. Default going forward: `groq`.

**Non-negotiable when adding or changing a provider:** every safety/quality mechanism
built for Ollama must be re-proven against the new provider, not assumed to transfer —
see workflow.md Stage 10 for the exact recalibration checklist. Specifically:
- The `MAX_OVERLAP` verbatim-restatement check was calibrated against qwen2.5:3b's
  specific tendency to quote context blocks verbatim. A different model may have a
  *different* failure mode this check doesn't catch — don't assume "still passes" means
  "still meaningful" without checking what it's actually catching.
- The contradiction-detection / evidence-gating logic must be re-run against the
  project's two historically hardest verification questions (the business-class
  contradiction question, the meal-allowance citation question) for any new provider.
- The "retrieved document text is untrusted data" system-prompt instruction must be
  confirmed present and effective in each new provider's actual message format, not
  just copy-pasted and assumed to work identically.

**Streaming contract is unchanged regardless of provider:** the validate-then-replay
design (never streaming an unvalidated answer to the client — see the historical
dangling-fragment and stuck-caret fixes this project already went through) must hold
for Groq and Gemini exactly as it did for Ollama, even though their chunk-streaming
granularity differs from Ollama's.

**Disclosed architectural fact:** when `LLM_PROVIDER` is `groq` or `gemini`, retrieved
document context (the actual policy content) is sent to that provider's cloud API as
part of the prompt. This is a real change from the all-local Ollama baseline and should
be treated as a known fact for anyone evaluating this project for use with real
sensitive documents, not just synthetic fixtures.

#### 4.1.1 Stage 10 Recalibration Results (Groq / qwen/qwen3.8-27b — measured 2026-10-04)

**Step 3 — Safety check (`verify_injection.py`):** 5/5 runs passed. The adversarial
document injection test — a context block containing `IGNORE PREVIOUS INSTRUCTIONS` —
did not leak into the generated answer in any run. The "retrieved text is untrusted data"
system-prompt rule is present and effective in the Groq message path.

**Step 3 — Quality check (`verify_generation.py --runs 3`):** 177/180 checks passed
across 3 runs × 10 questions × 6 checks each. The 3 failures were all the same
false-negative in the test fixture: the expected token `"1500"` was not found in the
answer `"$1,500."` because the comma in the formatted number breaks a bare-substring
match. The answer is factually correct; the fixture was wrong. Fixed: expected token
updated to `"1,500"`.

| Check | Result |
|-------|--------|
| Answer not a refusal | ✓ all 30 |
| Not a generation failure | ✓ all 30 |
| At least one citation | ✓ all 30 |
| Not a numbered block restatement | ✓ all 30 |
| Verbatim overlap < 0.30 | ✓ all 30 (worst: 0.113) |
| States documented facts | ✓ 27/30 (3 fixture false-negatives, fixed) |

**`MAX_OVERLAP = 0.30` threshold analysis:** qwen/qwen3.8-27b consistently produces
overlap scores well below the threshold (0.00–0.113 across all 30 answer/block pairs).
This confirms the threshold is not moot — the model synthesises rather than quotes —
but also that a verbatim-copy failure mode is less likely with this model than with
qwen2.5:3b. The threshold remains set to 0.30 (the gap between confirmed synthesis and
confirmed restatement) and is still meaningful as a safety net, especially during
retries where the model might quote more literally under constraint.

**Hardest historical questions — confirmed with new provider:**

- *"Can I book business class for a five-hour flight?"* — answered correctly in every
  run: `"No … economy class is mandatory for all flights under six hours."` with 1
  citation. Zero retries needed (qwen2.5:3b historically needed retries here).
- *"What is the daily meal allowance for an international trip?"* — answered correctly
  every run: `"100 USD"` with 1 citation. Zero retries.

**New failure mode observed:** none. The model did not hallucinate plausible-but-wrong
citations in any run, and did not produce answers without citations. The retry-on-
rejection mechanism was invoked on the travel-approval question (up to 13 retries
observed in logs) because the model initially restated policy with slightly high overlap
— the existing guard worked correctly.

**Step 4 — Latency comparison (measured 2026-10-04):**

Groq (`qwen/qwen3.8-27b`) — 6 requests across 3 questions, 2 repeats each:

| Metric | Groq value |
|--------|-----------|
| Avg wall-clock per question | **2.28 s** |
| Avg first-attempt model time | 0.92 s |
| Avg decode (eval) | 0.92 s |
| Avg prompt tokens | 1 379 |
| Avg generated tokens | 27 |
| Decode throughput | 28.9 tok/s |
| Requests that retried | 0/6 |

Ollama (`qwen2.5:3b`, CPU-only) baseline (from Stage 5 measurements): ~30–60 s per
turn (15–45 s model decode, plus 1–3 s retrieval). **Groq is ~13–26× faster wall-clock
for this project's actual prompt sizes.** This is a confirmed, concrete measurement for
this hardware and these question types as of 2026-10-04. API speeds change; re-measure
if latency becomes a concern again.

Note: one request in the latency batch (`Can I book business class?` repeat 2) took
4.53 s wall-clock due to a Groq network spike (3.56 s decode vs 0.38 s for the same
question repeat 1). The average above includes this outlier; median would be ~1.9 s.

---

## 5. Database Schema — V1

```
users              id, email, name
documents          id, filename, file_type, storage_path, status, created_at
document_chunks    id, document_id, chunk_index, page_number, section, qdrant_point_id
conversations      id, user_id, title, created_at
messages           id, conversation_id, role, content, created_at
citations          id, message_id, document_id, page_number, section, relevance_score, matched_text
```
`citations.matched_text` was added during Stage 6 (required for frontend highlighting,
per Section 6) — the schema above reflects the actual, current, live table. No
`permissions`, `document_versions`, or `audit_logs` tables in V1 — see Section 14 for
what the schema grows into when the admin dashboard is built.

---

## 6. Core API Endpoints — V1

```
POST   /api/v1/documents            upload → { document_id, status }
GET    /api/v1/documents            list documents
GET    /api/v1/documents/{id}       document detail + status

POST   /api/v1/chat/stream          SSE-streamed answer + citations
GET    /api/v1/conversations
GET    /api/v1/conversations/{id}
```

`GET /api/v1/documents/{id}/pages/{page}` (page text + bounding boxes for highlight)
was never built — the frontend's Evidence panel instead renders `citation.matched_text`
directly as the highlighted content, since it IS the literal cited substring (see
Section 11.4's documented deviation). This endpoint remains a real V2 enhancement if
full-page context display is ever wanted.

Final SSE event shape (as actually implemented):
```json
{
  "answer": "...",
  "has_sufficient_evidence": true,
  "citations": [
    {
      "id": "c1",
      "document_id": "doc_123",
      "document_name": "Travel Policy.pdf",
      "page": 43,
      "section": "6.2",
      "matched_text": "six months of continuous service",
      "relevance": 0.91
    }
  ]
}
```
`matched_text` is required — the frontend highlights this exact string in the Evidence
panel. `page` is nullable for non-paginated source formats (XLSX, CSV).

---

## 7. Frontend Tech Stack (fixed) — V1

| Concern | Choice |
|---|---|
| Framework | React 19 + TypeScript, **Vite** |
| Styling | **Tailwind CSS v4** |
| Component system | **shadcn/ui**, Base UI primitives, **Nova** style preset |
| Animation | **Framer Motion** — functional use only, per Section 9 |
| Server state | **TanStack Query** |
| Client state | **Zustand** |
| Routing | React Router |
| Icons | **Lucide** |
| Streaming | native `fetch` stream reader for SSE (`lib/sse.ts`) |
| Markdown | `react-markdown` + `remark-gfm` for answer text |
| Fonts | `@fontsource-variable/geist` (body/UI), `@fontsource-variable/fraunces` (display) |

---

## 8. Zustand Store Shape — V1 (as implemented)

```ts
// workspaceStore.ts
interface WorkspaceStore {
  activeWorkspaceId: string | null;   // "HR" | "Finance" | "Security" — cosmetic V1
                                       // grouping; backend has no workspace field, so
                                       // all documents live in one implicit workspace.
  documents: Document[];
  setActiveWorkspace: (id: string) => void;
}

// conversationStore.ts
interface ConversationStore {
  conversationId: string | null;
  messages: Message[];               // includes streaming partial message
  isStreaming: boolean;
  appendToken: (token: string) => void;
  setCitations: (messageId: string, citations: Citation[]) => void;
  cancelStream: (messageId?: string) => void;  // added post-launch: resets isStreaming,
                                       // clears streamingMessageId, and honestly labels
                                       // a cancelled message ("Generation cancelled.")
                                       // instead of leaving the UI permanently stuck —
                                       // see Section 11.3's cancelled-state note.
}

// evidenceStore.ts   ← drives the third pane / document viewer
interface EvidenceStore {
  isOpen: boolean;
  activeCitation: Citation | null;
  openCitation: (citation: Citation) => void;
  close: () => void;
}

// themeStore.ts   ← drives Section 10's theme system
interface ThemeStore {
  theme: "light" | "dark";
  toggle: () => void;
  // on init: read localStorage, fall back to prefers-color-scheme, then apply
  // by setting document.documentElement.dataset.theme = theme
}
```
The Evidence panel is driven entirely by `evidenceStore` — clicking any citation chip
anywhere in the app calls `openCitation()`, nothing else needs to know about the panel.
`themeStore` is read once at app boot to set `data-theme` on `<html>`, then only touched
by the theme toggle control in the top bar. None of these stores use Zustand's
`persist` middleware — only `policy-rag-theme` and `rag_active_conversation_id` are
ever written to `localStorage`, directly, by the relevant store/hook.

---

## 9. Visual Identity Principles — V1

Do not build a generic "ChatGPT clone" or template SaaS dashboard (sidebar + navbar +
plain card grid + one centered chat box, all default zinc/slate gray). That look is an
instant tell of an undirected AI-generated UI and is a failure state for this project.

1. **Typography carries the branding.** Fraunces (serif/display) for the app name and
   section headings, paired with Geist (clean sans) for body/UI text. Don't leave the
   default shadcn font stack with no personality.
2. **One deliberate accent color family**, used sparingly and consistently (citation
   chips, active workspace indicator, streaming caret, highlight background) — see
   Section 10 for the exact values. Never the default shadcn zinc/slate palette with
   zero accent.
3. **No decorative gradients, glow effects, or "AI purple/blue" hero treatments.** This
   is a professional reading/verification tool, closer to a well-designed IDE or
   research tool than a marketing landing page.
4. **Density over whitespace-padding.** Favor a readable, information-dense layout
   (compact rows, tight but legible line-height) over oversized template-SaaS padding —
   pairs naturally with the Nova preset.
5. **Motion is functional only** (Framer Motion): evidence panel slide-in/out, citation
   chip hover, streaming caret pulse, upload progress transitions, theme cross-fade.
   No page-load fade-ins, bouncing cards, or animate-everything patterns.
6. **Empty/loading states are designed, not placeholders.** Every "nothing here yet"
   state gets real copy and layout, not a spinner or "No data" text.

---

## 10. Color System & Theming — V1

**Direction:** a warm, editorial "espresso and brass" palette — dark theme reads like
rich dark-roast coffee/leather, light theme reads like warm ivory paper, not clinical
white. Same accent hue family in both modes so switching themes never feels like a
different product.

### 10.1 Dark theme — "Espresso" (default)

| Token | Hex | Used for |
|---|---|---|
| `--bg` | `#1C1410` | app background |
| `--surface` | `#241A14` | sidebar, panels, cards |
| `--surface-elevated` | `#2C2019` | evidence panel, popovers, dropdowns |
| `--border` | `#3A2A21` | dividers, card borders, input borders |
| `--text-primary` | `#F5EDE4` | body text, headings |
| `--text-muted` | `#B5A395` | secondary text, timestamps, placeholders |
| `--accent` | `#D9A55C` | active states, citation chips, buttons, streaming caret |
| `--accent-hover` | `#E8B96F` | hover state of the above |
| `--highlight` | `rgba(217,165,92,0.18)` | evidence matched-text background |
| `--success` | `#7FA871` | "ready" status, muted sage (not neon green) |
| `--warning` | `#C97B63` | "insufficient evidence" / low-confidence / cancelled states |

### 10.2 Light theme — "Ivory"

| Token | Hex | Used for |
|---|---|---|
| `--bg` | `#FBF7F1` | app background (warm ivory, not `#FFFFFF`) |
| `--surface` | `#F4EDE3` | sidebar, panels, cards |
| `--surface-elevated` | `#FFFFFF` | evidence panel, popovers, dropdowns |
| `--border` | `#E3D8C8` | dividers, card borders, input borders |
| `--text-primary` | `#2A1F17` | body text, headings (echoes dark theme's bg tone) |
| `--text-muted` | `#6E5D4E` | secondary text, timestamps, placeholders |
| `--accent` | `#8A5A24` | active states, citation chips, buttons (darker shade for AA contrast on light bg) |
| `--accent-hover` | `#A66B2C` | hover state of the above |
| `--highlight` | `rgba(154,107,46,0.14)` | evidence matched-text background |
| `--success` | `#4F7A45` | "ready" status |
| `--warning` | `#A8503A` | "insufficient evidence" / low-confidence / cancelled states |

Both palettes share the same hue family (warm brown/amber) at different lightness —
verified live, exact hex match, in `verify_full_stack.py` Check 14.

### 10.3 Implementation

CSS custom properties switched by a `data-theme` attribute on `<html>`:

```css
:root, [data-theme="light"] {
  --bg: #FBF7F1;
  --surface: #F4EDE3;
  /* ...rest of the Ivory table above */
}

[data-theme="dark"] {
  --bg: #1C1410;
  --surface: #241A14;
  /* ...rest of the Espresso table above */
}
```

Mapped into Tailwind v4's `@theme` block (`bg-surface`, `text-primary`, `border-default`,
`bg-accent`, etc.):

```css
@theme {
  --color-bg: var(--bg);
  --color-surface: var(--surface);
  --color-surface-elevated: var(--surface-elevated);
  --color-border: var(--border);
  --color-text-primary: var(--text-primary);
  --color-text-muted: var(--text-muted);
  --color-accent: var(--accent);
  --color-accent-hover: var(--accent-hover);
  --color-success: var(--success);
  --color-warning: var(--warning);
}
```

`themeStore.ts` sets `document.documentElement.dataset.theme` on toggle and persists
the choice to `localStorage`; default on first visit follows `prefers-color-scheme`,
defaulting to dark if unavailable. Theme toggle lives in the top bar — sun/moon Lucide
icon, ~150ms Framer Motion opacity cross-fade, verified live with zero layout shift.

### 10.4 Typography pairing

- **Display/headings**: Fraunces (`@fontsource-variable/fraunces`), used only for
  headings and the wordmark, not body text.
- **Body/UI text**: Geist (`@fontsource-variable/geist`, Nova preset default), kept
  as-is for dense UI text.

### 10.5 Where color is NOT used

Accent is reserved for interactive/active elements and the evidence highlight only.
Structural chrome (sidebar, panels, borders) stays neutral (`bg`, `surface`, `border`
tokens) so the warm accent actually stands out when it appears.

---

## 11. Component-Level UI Layouts — V1

### 11.1 App shell (full workspace)

```
┌──────────────────────────────────────────────────────────────┐
│  POLICY INTELLIGENCE              Search   ☀/☾   Profile     │
├───────────────┬──────────────────────────────┬───────────────┤
│               │                              │               │
│ WORKSPACES    │       ASK YOUR POLICIES      │   EVIDENCE    │
│               │                              │               │
│ ● HR          │  ┌────────────────────────┐  │  Source #1    │
│ ● Finance     │  │ What is the travel     │  │  Page 43     │
│ ● Security    │  │ reimbursement limit?   │  │  Section 6.2 │
│               │  └────────────────────────┘  │               │
│ DOCUMENTS     │                              │  Source #2    │
│               │  AI                          │  Page 44     │
│ Travel Policy │  The policy states that...   │  Section 6.2.1│
│ HR Handbook   │  SOURCES [chip] [chip]        │               │
│ Security      │  ──────────────────────────  │               │
│               │                              │               │
│ + Upload      │  Ask a follow-up...          │               │
│               │                              │               │
└───────────────┴──────────────────────────────┴───────────────┘
```
- Left pane fixed ~240px on `bg-surface`. Center pane fluid, max reading width ~720px
  on `bg-bg`. Right pane ~360px on `bg-surface-elevated`, **collapsed to zero width by
  default** — this diagram shows it populated after a citation click. `☀/☾` is the
  theme toggle described in 10.3.

### 11.2 Left sidebar — detail states (all implemented and verified)

**Default (documents ready):**
```
┌───────────────┐
│ WORKSPACES     │
│ ● HR          ← active (accent dot + accent text)
│ ○ Finance      │
│ ○ Security     │
│───────────────│
│ DOCUMENTS      │
│ 📄 Travel Policy      ✓ ready
│ 📄 HR Handbook        ✓ ready
│ 📄 Security Policy    ✓ ready
│───────────────│
│ + Upload document     │
└───────────────┘
```

**While a document is processing:**
```
│ 📄 New Leave Policy.pdf
│    ▓▓▓▓▓▓░░░░  Parsing…
```
Status text cycles through real pipeline stages (Uploading → Parsing → Chunking →
Embedding → Indexing → Ready). Progress fill uses `--accent`.

**Dropzone disabled during active ingestion:** added post-launch. The backend serializes
document conversion behind a single async lock in V1 (Section 14's note / Section 18.2)
— a second upload accepted while one is still processing would silently queue with no
feedback. The dropzone instead renders a dimmed, non-interactive placeholder
("Processing… upload will resume shortly") while any document is in a non-terminal
status, and re-enables once it reaches `ready`/`failed`.

**Failed state:**
```
│ 📄 Bad Upload.pdf     ✗ Ingestion failed. This document
│                         cannot be searched or cited.
```

**Empty state (no documents uploaded yet):**
```
│ DOCUMENTS              │
│                        │
│   No documents yet.    │
│   Upload a policy to   │
│   start asking          │
│   questions.             │
│                        │
│   [ + Upload document ]│
```

### 11.3 Center pane — chat thread

```
                    ASK YOUR POLICIES

  What is the travel reimbursement limit?              (user, right-aligned)

  The policy states that employees must submit claims
  within 30 days of completing the trip. Approval
  requires manager sign-off. ▌                           (AI, plain text, streaming caret)

  SOURCES
  ┃ Travel Policy · p.43 · Section 6.2
  ┃ Travel Policy · p.44 · Section 6.2.1

  ┌──────────────────────────────────────────┐
  │ Ask a follow-up...                    ➤ │
  └──────────────────────────────────────────┘
```
- AI answers render as plain text on `bg-bg` — no chat-bubble container.
- `▌` is the animated streaming caret (`text-accent`, Framer Motion opacity pulse)
  shown only while that message is actively streaming.

**Deviation (as built) — citations render as a labelled `Sources` row, not inline.**
The original sketch placed chips mid-sentence. That is not achievable against the
current backend contract: `done.answer` has the backend's `[n]` markers stripped
server-side (the same change that fixed the "No, according to [1], economy…"
dangling-fragment bug), so the exact character offset of each citation within the
final text is not recoverable by the client. As built, each AI message's citations
render in a `Sources` row directly beneath that message's text, using the same
`CitationChip` component and the same Evidence-panel behavior as originally designed.
Inline chips become possible only if the backend is changed to preserve marker offsets
in the streamed text — not a frontend-only change.

**Cancelled generation (added post-launch):** a user can stop an in-flight answer via
a visible Stop button or Escape. On cancellation, the stream resets honestly and
immediately — input re-enables, placeholder reverts, the pulsing caret is removed, and
the message is labeled plainly:
```
  Generation cancelled.                      (text-muted, 13px, italic)
```
This is NOT a silent failure state (per workflow.md Golden Rule 10) — it's an explicit
user action with an honest result, styled quietly rather than as an error.

**No-evidence-found response (styled with `--warning`, still a normal AI message):**
```
  I couldn't find this in the uploaded documents.
  You may want to check with HR directly, or upload
  the relevant policy if you have it.
```

**Empty conversation state:**
```
                    ASK YOUR POLICIES

        Ask a question about any uploaded policy —
        answers will always cite the exact source.

        Try: "What is the leave carry-forward limit?"

  ┌──────────────────────────────────────────┐
  │ Ask a question...                     ➤ │
  └──────────────────────────────────────────┘
```

### 11.4 Right pane — Evidence panel

**Closed (default state — zero width, not just hidden content.)**

**Open — citation list view (when an answer has multiple citations):**
```
┌───────────────┐
│ EVIDENCE       │
│───────────────│
│ Source #1      │
│ Travel Policy  │
│ Page 43        │
│ Section 6.2    │
│───────────────│
│ Source #2      │
│ Travel Policy  │
│ Page 44        │
│ Section 6.2.1  │
└───────────────┘
```

**Open — expanded passage view (after clicking a specific source):**
```
┌────────────────────────────┐
│ ← Back to sources           │
│                            │
│ DOCUMENT VIEW               │
│ Travel Policy                │
│ ──────────────────────────  │
│ Page 27 · 4. Eligibility     │
│                            │
│ Employees who have completed │
│ ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓ │
│ six months of continuous     │
│ service                      │
│ ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓ │
│ are eligible for...          │
│ ──────────────────────────  │
└────────────────────────────┘
```
- `▓` marks the highlighted span matching `citation.matched_text` — a real `<mark>`-
  style span using the `--highlight` token. This IS the content shown; there is no
  separate page-context fetch (see Section 6's note on the missing pages endpoint).
- Panel slides in from the right (Framer Motion `x` transform, ~250ms ease-out).
  Switching between sources — including across different source documents/formats
  (PDF ↔ XLSX ↔ CSV) — cross-fades the content without flickering stale content.

### 11.5 Upload flow

```
┌──────────────────────────────────────┐
│                                      │
│         Drop a policy document here │
│           or click to browse         │
│                                      │
│      PDF · DOCX · XLSX · CSV        │
└──────────────────────────────────────┘
```
Dashed `--border` outline, `--accent` on drag-over. After drop → immediately shows the
sidebar processing state (11.2) — no separate modal or full-page "uploading" screen.
Disabled (11.2) while any other document is actively ingesting.

---

## 12. Frontend Folder Structure — V1 (as implemented)

```
frontend/src/
├── components/
│   ├── ui/                shadcn-generated primitives (unmodified)
│   ├── chat/               MessageThread.tsx, MessageBubble.tsx, CitationChip.tsx, ChatInput.tsx
│   ├── evidence/           EvidencePanel.tsx, HighlightedPassage.tsx, SourceList.tsx
│   ├── documents/          DocumentList.tsx, UploadDropzone.tsx, DocumentStatusBadge.tsx
│   └── layout/             AppShell.tsx, WorkspaceSidebar.tsx, TopBar.tsx, ThemeToggle.tsx
├── pages/
│   ├── Chat.tsx
│   └── Documents.tsx
├── stores/                 workspaceStore.ts, conversationStore.ts, evidenceStore.ts, themeStore.ts
├── hooks/                  useChatStream.ts, useUploadDocument.ts
├── lib/                    api.ts, sse.ts, utils.ts, mock/fixtures.ts (dev-only, VITE_MOCK_API gated)
└── types/                  document.ts, chat.ts, citation.ts
```

---

## 13. Policy Comparison — V2 (fully specified, do not build yet)

**Purpose:** let a user compare two versions of the same policy and see exactly what
changed, with the same citation-backed trust as chat answers.

### Layout
```
                     POLICY COMPARISON

  2025                              2026

  Eligibility                       Eligibility
  ────────────                       ──────────

  6 months                           3 months
  ██████████                         ██████████

                                ↓

                         CHANGE DETECTED
```
- Two-column side-by-side layout, one version per column, sections aligned by heading.
- Changed sections use the `--highlight` token plus a "Change detected" indicator.
- Summary strip: `Changed sections: 8 · Added: 3 · Removed: 2 · Modified: 3`.
- Clicking a changed section reuses the Evidence-panel pattern from Section 11.4.

### Backend additions required (V2 only)
```
document_versions   id, document_id, version_label, storage_path, created_at
```
Comparison logic: retrieve matching sections by heading path, diff at the paragraph
level, have the LLM summarize the nature of each change with citations into both
versions.

### API (V2)
```
POST /api/v1/comparisons     { document_id, version_a, version_b } → diff + summary
```

---

## 14. Admin Dashboard — V2 (fully specified, do not build yet)

**Purpose:** a separate, role-gated view for authorized users (company/policy heads) to
monitor the system and manage documents organizationally — not the regular chat
workspace, never visible to regular users.

### Access model (V2 minimum — do not build full RBAC/SSO, see Section 2)
- Add `role` column to `users`: `member | admin`.
- A FastAPI dependency checks `current_user.role == "admin"` on every
  `/api/v1/admin/*` route — 403 otherwise.
- Frontend: "Admin" nav item and `/admin` route only render when `role == admin`.

### Layout
```
┌──────────────────────────────────────────────────────────────┐
│  POLICY INTELLIGENCE · ADMIN                        ● Kartik │
├───────────────┬────────────────────────────────────────────────┤
│               │                                                │
│ OVERVIEW      │   SYSTEM OVERVIEW                              │
│ Documents     │   ┌───────────┬───────────┬───────────┐        │
│ Users         │   │ Documents │ Queries    │ Avg conf.  │        │
│ Query Logs    │   │    24     │  1,204     │   91%      │        │
│               │   └───────────┴───────────┴───────────┘        │
│               │                                                │
│               │   RECENT UPLOADS                               │
│               │   Travel Policy.pdf     ✓ ready    2h ago       │
│               │                                                │
│               │   LOW-CONFIDENCE QUESTIONS (needs review)      │
│               │   "What is the WFH stipend?"   62% confidence  │
└───────────────┴────────────────────────────────────────────────┘
```
- **Documents view:** full table (status, upload date, uploaded-by, chunk count, delete).
- **Users view:** list of users and roles, promote to `admin`.
- **Query Logs view:** searchable table of past questions with confidence and cited
  documents — lets a policy head spot gaps (frequent low-confidence questions = a
  policy that needs clarifying or uploading).
- Same color system and typography as Sections 9–10. Low-confidence rows use `--warning`.

### Backend additions required (V2 only)
```
users.role                    add column: 'member' | 'admin'
query_logs   id, conversation_id, question, has_sufficient_evidence,
             top_relevance_score, cited_document_ids, created_at
```

### API (V2)
```
GET  /api/v1/admin/overview        aggregate counts
GET  /api/v1/admin/documents        full document table
GET  /api/v1/admin/users            list + roles
PATCH /api/v1/admin/users/{id}      update role
GET  /api/v1/admin/query-logs       paginated, filterable by confidence
```

---

## 15. Environment Variables (backend/.env)

```
DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5434/rag_policy
QDRANT_URL=http://localhost:6333
REDIS_URL=redis://localhost:6379/0

LLM_PROVIDER=groq                 # ollama | groq | gemini — see Section 4.1
EMBEDDING_MODEL=sentence-transformers/all-MiniLM-L6-v2

# Provider-specific (set only the block matching LLM_PROVIDER)
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=qwen2.5:3b

GROQ_API_KEY=<set in .env only, never committed, placeholder in .env.example>
GROQ_MODEL=<look up current available model name — do not hardcode from memory>

GOOGLE_API_KEY=<set in .env only, never committed, placeholder in .env.example>
GEMINI_MODEL=<look up current available model name — do not hardcode from memory>
```

> **Note on Postgres port**: the container maps `5434:5432` (not the default `5432:5432`)
> because a native Windows PostgreSQL service occupies 5432 on this development machine.
> `docker-compose.yml` and `.env` are both set to `5434` permanently — no manual
> override file needed for a normal cold start.

> **Note on Redis in V1**: Redis is containerized, monitored, and verified healthy via
> `/health`. Per V1 scope (Sections 2 and 3), document ingestion and conversation state
> execute in-process within FastAPI without an external message broker or distributed
> cache. Real application traffic on Redis is deliberately zero in V1 beyond `/health`
> connectivity verification; it is provisioned in advance for V2 task queues and caching.

> **Note on cloud LLM providers**: when `LLM_PROVIDER` is `groq` or `gemini`, uploaded
> document content is sent to that provider's API as part of the retrieval context in
> each request. This is a real, disclosed change from the all-local Ollama baseline —
> see Section 4.1.

---

## 16. Definition of Done for V1

A working demo where a user can:
1. Upload a PDF/DOCX/XLSX/CSV policy document and see it reach "ready" status
2. Ask a question in chat and get a streamed answer with citation chips
3. Click a citation and see the exact source passage highlighted in the Evidence panel
4. Get an honest "not found in the documents" response when the answer isn't there
5. Switch between the Espresso dark theme and Ivory light theme with no layout jumps
6. Look at the UI and not immediately think "this is a generic AI-generated dashboard"

**Status: met — see Section 18 for the dated, evidenced scorecard.**

Items 2 and 4 must be **re-verified against any newly configured LLM provider** before
that provider is considered production-default — see Section 4.1 and workflow.md's
Stage 10 checklist. A provider swap that hasn't re-cleared items 2 and 4 is not done.

Only after this is solid should Section 13 (Comparison) or Section 14 (Admin Dashboard)
be started.

---

## 17. Revision History

- **v1 — Scope & stack:** established V1 boundaries, backend RAG pipeline, DB schema,
  core API contract.
- **v2 — UI direction:** three-pane app shell, rules against a generic "AI-generated
  dashboard" look, Zustand store shapes.
- **v3 — Component-level layouts:** per-component wireframes for every state.
- **v4 — V2 features specified early:** Policy Comparison and Admin Dashboard fully
  designed, kept explicitly out of V1 build scope.
- **v5 — Color system:** concrete Espresso/Ivory palette, CSS-variable/Tailwind
  implementation, typography pairing.
- **v6 — V1 Completion & Definition of Done Signoff (2026-10-03):** verified all 6 DoD
  requirements with live automated tests and end-to-end browser walkthroughs. Closed
  XLSX/CSV ingestion gaps, documented stream cancellation recovery, recorded accepted
  V1 operational boundaries.
- **v7 — LLM provider flexibility (active):** documented the `LLMProvider` abstraction
  (Section 4.1), added Groq/Gemini as selectable providers alongside Ollama, updated
  environment variables (Section 15) and the Postgres-port fix, folded the
  dropzone-disable-during-ingestion and stream-cancellation UI behavior into Sections
  11.2/11.3 as now-permanent, documented behavior rather than leftover session notes.

---

## 18. V1 Status & Definition of Done Verification (Dated: 2026-10-03)

### 18.1 Definition of Done Scorecard

| # | Requirement | Status | Evidence / Verification Notes |
|---|---|---|---|
| 1 | **Upload PDF/DOCX/XLSX/CSV document to "ready" status** | **PASS** | All four formats verified end-to-end live (`verify_full_stack.py` Checks 5–6); Docling preserves tables as markdown; dense + sparse vectors indexed. |
| 2 | **Chat question with streamed answer and citation chips** | **PASS** | Checks 7–8, 13. Tokens stream via SSE; `Sources` row renders citation chips (see Section 11.3's documented inline-vs-row deviation). **Must be re-verified per provider — see Section 16.** |
| 3 | **Click citation to view highlighted passage in Evidence panel** | **PASS** | Check 13 (Playwright `<mark>` assertion). Panel slides in, cross-fades across sources including across formats. |
| 4 | **Honest "not found in documents" refusal response** | **PASS** | Check 9. Cross-encoder gate correctly short-circuits with zero hallucination. **Must be re-verified per provider — see Section 16.** |
| 5 | **Switch Espresso dark / Ivory light with no layout jumps** | **PASS** | Check 14. 100% exact hex token match both palettes, zero layout shift. |
| 6 | **Distinctive non-generic editorial visual identity** | **PASS** | Fraunces + Geist pairing, warm coffee/paper colorway, no generic AI glows/cards, designed empty/processing/cancelled states. |

### 18.2 Known & Accepted V1 Operational Boundaries

1. **CPU-bound Ollama latency (baseline, being addressed):** local CPU-only Ollama
   (`qwen2.5:3b`) generation takes ~30–60s/turn, Docling parsing ~30–40s for rich PDFs.
   This is the direct motivation for Section 4.1's provider migration — not fully
   resolved as of this revision; Ollama remains the proven, fallback baseline until
   Groq/Gemini are recalibrated and verified per workflow.md Stage 10.
2. **Redis provisioned for V2:** zero real V1 application traffic beyond `/health`.
3. **Evidence highlighting via `matched_text`, not full-page rendering:** the
   `pages/{page}` endpoint was never built; the Evidence panel shows the literal cited
   excerpt directly. Full PDF canvas rendering remains a real V2 enhancement.
4. **Single-process ingestion lock:** document conversion runs inline within FastAPI
   behind an async mutex to prevent concurrent CPU exhaustion; the frontend disables
   the upload dropzone while any document is non-terminal rather than silently queuing
   a confusing second upload. Distributed worker queues remain V2 scope.
5. **Cloud provider data egress (new, see Section 4.1):** once `LLM_PROVIDER` is set to
   `groq` or `gemini`, document content leaves the local machine as part of each
   request's context — disclosed, not hidden.