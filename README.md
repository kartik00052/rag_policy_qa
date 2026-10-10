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
