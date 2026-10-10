# Policy RAG Assistant

A Retrieval-Augmented Generation (RAG) application for asking natural-language
questions about company policy documents (PDF, DOCX, XLSX, CSV). It retrieves the
relevant passages, answers with an LLM, and **always cites the source document, page,
and section** — it never answers from the model's own knowledge.

**Core product rule:** if the retrieved evidence doesn't clearly answer the question,
the system says so instead of guessing. Every answer can be traced back to the exact
source passage, highlighted in an Evidence panel.

## How it works

```
Upload document → Parse & chunk → Embed → Store in vector DB
User asks a question → Retrieve relevant chunks → Rerank → LLM answers with citations
```

- **Ingestion:** Docling parses PDF/DOCX/XLSX/CSV into structured elements (headings,
  tables preserved as markdown), chunked by section rather than fixed windows.
- **Retrieval:** hybrid dense (semantic) + sparse BM25 (lexical) search over Qdrant,
  fused with Reciprocal Rank Fusion, then reranked by a cross-encoder.
- **Generation:** a LangGraph flow (`retrieve → rerank → check_evidence → generate →
  cite`) streams a grounded, cited answer over SSE, or short-circuits with an honest
  "not found in the documents" response when evidence is insufficient.

## Tech stack

| Layer | Choice |
|---|---|
| Frontend | React 19 + TypeScript, Vite, Tailwind CSS v4, shadcn/ui, TanStack Query, Zustand |
| API | FastAPI (async), Pydantic v2, SSE streaming |
| Orchestration | LangGraph |
| LLM | Pluggable via LangChain — **Groq** (default), Gemini, Ollama (local fallback), selected by `LLM_PROVIDER` |
| Embeddings | FastEmbed `bge-small-en-v1.5` (dense) + BM25 (sparse) |
| Reranker | Cross-encoder `cross-encoder/ms-marco-MiniLM-L-6-v2` |
| Data | PostgreSQL 16 (metadata, chat log), Qdrant (chunks + vectors), Redis (provisioned) |
| Tooling | `uv` (backend), npm (frontend), Docker Compose for infrastructure |

## Repository layout

```
policy_rag/
├── backend/          FastAPI app, RAG pipeline, ingestion, migrations, tests
├── frontend/         React + Vite single-page app
├── docs/             Backend architecture & learning guide
├── docker-compose.yml  Postgres, Qdrant, Redis, backend, frontend
├── project.md        Single source of truth: scope, stack, schema, UI
└── workflow.md       Agent workflow, build order, conventions
```

`project.md` is the authoritative spec for scope and design decisions; `workflow.md`
governs how work is done. Read both before contributing.

> When `LLM_PROVIDER` is `groq` or `gemini`, retrieved document content is sent to that
> provider's cloud API as part of the prompt. This is a disclosed change from the
> all-local Ollama baseline — see `project.md` Section 4.1.

## Frontend

A React 19 + TypeScript single-page app (`frontend/src`) with a three-pane workspace:

- **Left sidebar** (`components/layout/`): workspace groups and the document list, with
  real per-stage ingestion status (Uploading → Parsing → Chunking → Embedding →
  Indexing → Ready) and a dropzone that disables while a document is still processing.
- **Center** (`components/chat/`): the streamed chat thread with citation chips and a
  functional streaming caret; empty, refusal, and cancelled states are all designed.
- **Right** (`components/evidence/`): the Evidence panel, collapsed to zero width by
  default and opened on any citation click to highlight the exact cited passage.

State is split between **Zustand** stores (`stores/`: `workspace`, `conversation`,
`evidence`, `theme`) and **TanStack Query** for server data. Networking lives entirely
in `lib/api.ts` (with a dev-only `VITE_MOCK_API` fixture mode) and SSE parsing in
`lib/sse.ts`; `hooks/useChatStream.ts` owns the stream lifecycle. Both Espresso (dark)
and Ivory (light) themes are implemented via CSS custom properties driven by a
`data-theme` attribute.

## Local development

**Infrastructure** (Postgres, Qdrant, Redis — plus containerized backend/frontend):

```bash
docker compose up -d
```

**Backend** (Python 3.11, run with `uv`):

```bash
cd backend
uv sync
uv run alembic upgrade head
uv run python run.py            # serves on http://localhost:8000
```

On Windows, `backend/scripts/start_server.ps1` wraps this with the required asyncio
selector-loop setup and a health check.

**Frontend** (Node, Vite):

```bash
cd frontend
npm install
npm run dev                     # serves on http://localhost:5173
```

Copy `.env.example` to `.env` in both `backend/` and `frontend/` and fill in the values
(Postgres port is pinned to `5434` to avoid a native-Windows conflict). Secrets such as
`GROQ_API_KEY` / `GOOGLE_API_KEY` go in `.env` only and are never committed.
