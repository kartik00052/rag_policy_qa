/**
 * API client (PROJECT.md Sections 6-7).
 *
 * Every network call in the app goes through here. When `VITE_MOCK_API` is set
 * the functions return fixtures instead, with the same signatures and the same
 * shapes, so no component branches on which mode it is running in - that is the
 * whole point of this layer.
 *
 * Errors are real `ApiError`s carrying the backend's `detail`, never swallowed
 * (workflow.md Golden Rule 10). A policy tool that silently shows nothing when
 * the backend is down is worse than one that says so.
 */

import type {
  ChatFinal,
  ChatRequest,
  ConversationDetail,
  ConversationSummary,
} from "@/types/chat";
import type {
  DocumentDetail,
  DocumentSummary,
  DocumentUploadResponse,
} from "@/types/document";
import { ACCEPTED_SUFFIXES } from "@/types/document";

const API_URL = (import.meta.env.VITE_API_URL as string | undefined) ?? "";

/**
 * Mock mode is opt-in and dev-only. Two guards rather than one: the flag must
 * be set *and* the build must not be a production build, so a stray
 * `VITE_MOCK_API=true` in a deployed `.env` cannot serve fixtures to real users.
 */
export const MOCK_ENABLED =
  import.meta.env.DEV && import.meta.env.VITE_MOCK_API === "true";

/** HTTP status plus the backend's message, for a visible error state. */
export class ApiError extends Error {
  readonly status: number;
  readonly detail: string;

  constructor(status: number, detail: string) {
    super(detail);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

function url(path: string): string {
  return `${API_URL}${path}`;
}

/**
 * Reads the backend's error body. FastAPI's convention is `{"detail": "..."}`
 * but validation errors return a list of objects, so both shapes are handled
 * rather than rendering "[object Object]" into an error state.
 */
async function toApiError(response: Response): Promise<ApiError> {
  let detail = `Request failed with status ${response.status}`;
  try {
    const body = (await response.json()) as { detail?: unknown };
    if (typeof body.detail === "string") detail = body.detail;
    else if (Array.isArray(body.detail)) {
      detail = body.detail
        .map((item) =>
          typeof item?.msg === "string" ? item.msg : JSON.stringify(item),
        )
        .join("; ");
    }
  } catch {
    // Body was not JSON. The status-based message stands.
  }
  return new ApiError(response.status, detail);
}

// ---------------------------------------------------------------------------
// Real network layer
// ---------------------------------------------------------------------------

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(url(path), init);
  } catch (cause) {
    // A network-level failure (backend down, CORS, refused connection) never
    // produces a Response, so this is where the app's "backend unavailable"
    // state originates.
    const reason = cause instanceof Error ? cause.message : String(cause);
    throw new ApiError(0, `Cannot reach the backend at ${API_URL}. ${reason}`);
  }
  if (!response.ok) throw await toApiError(response);
  return (await response.json()) as T;
}

// ---------------------------------------------------------------------------
// Documents
// ---------------------------------------------------------------------------

interface DocumentListResponse {
  documents: DocumentSummary[];
  total: number;
}

export async function listDocuments(): Promise<DocumentSummary[]> {
  if (MOCK_ENABLED) {
    // The mock list is stateful, not a static array: it includes documents
    // added by `uploadDocument` and advances them through the pipeline, so the
    // polling loop is genuinely exercised rather than reading fixed rows.
    const { mockList } = await import("./mock/documentStore");
    return mockList();
  }
  const body = await request<DocumentListResponse>("/documents");
  return body.documents ?? [];
}

export async function getDocument(documentId: string): Promise<DocumentDetail> {
  if (MOCK_ENABLED) {
    // Resolved from the stateful store, not a fixed fixture: a hardcoded
    // detail made every document report the travel policy's stage position, so
    // a stale per-document poller would look correct while being wrong for
    // every document except one.
    const { mockGet, mockDefaultDetail } = await import("./mock/documentStore");
    return mockGet(documentId) ?? mockDefaultDetail();
  }
  return request<DocumentDetail>(`/documents/${documentId}`);
}

export async function uploadDocument(file: File): Promise<DocumentUploadResponse> {
  if (MOCK_ENABLED) {
    // Rejects unsupported types the same way the real backend's 400 does, so
    // the dropzone's request-failure state is exercised in mock mode rather
    // than only against a live server. Drag-and-drop bypasses the input's
    // `accept` filter, so this is a genuinely reachable path.
    const suffix = file.name.slice(file.name.lastIndexOf(".")).toLowerCase();
    if (!ACCEPTED_SUFFIXES.includes(suffix as (typeof ACCEPTED_SUFFIXES)[number])) {
      throw new ApiError(
        400,
        `Unsupported file type "${suffix || file.name}". Upload a PDF, DOCX, XLSX, or CSV.`,
      );
    }
    const { mockUpload } = await import("./mock/documentStore");
    return mockUpload(file.name, suffix.replace(".", ""));
  }
  const form = new FormData();
  form.append("file", file);
  return request<DocumentUploadResponse>("/documents", { method: "POST", body: form });
}

// ---------------------------------------------------------------------------
// Conversations
// ---------------------------------------------------------------------------

interface ConversationListResponse {
  conversations: ConversationSummary[];
  total: number;
}

export async function listConversations(): Promise<ConversationSummary[]> {
  if (MOCK_ENABLED) {
    const { MOCK_CONVERSATIONS } = await import("./mock/fixtures");
    return MOCK_CONVERSATIONS.map((conversation) => ({ ...conversation }));
  }
  const body = await request<ConversationListResponse>("/conversations");
  return body.conversations ?? [];
}

export async function getConversation(
  conversationId: string,
): Promise<ConversationDetail> {
  if (MOCK_ENABLED) {
    const { MOCK_CONVERSATION_DETAIL } = await import("./mock/fixtures");
    return { ...MOCK_CONVERSATION_DETAIL, id: conversationId };
  }
  return request<ConversationDetail>(`/conversations/${conversationId}`);
}

// ---------------------------------------------------------------------------
// Chat stream
// ---------------------------------------------------------------------------

export interface ChatStreamHandle {
  /** Resolves with the authoritative `done` payload once the stream ends. */
  done: Promise<ChatFinal>;
  /** Aborts the in-flight request. */
  cancel: () => void;
}

export interface ChatStreamCallbacks {
  onToken: (text: string) => void;
  /** Called for a server-sent `error` event, and for transport failures. */
  onError: (error: ApiError) => void;
}

/**
 * Opens the SSE stream for one question.
 *
 * In mock mode this replays the fixture answer token by token with a short
 * delay, so the streaming path, the caret and the citation chips can be
 * exercised without the backend's 20-45s per-answer wait. The delay is
 * deliberately much faster than real generation - the point is to prove the
 * rendering, not to simulate the latency.
 */
export function streamChat(
  request_: ChatRequest,
  callbacks: ChatStreamCallbacks,
): ChatStreamHandle {
  if (MOCK_ENABLED) return mockStream(request_, callbacks);

  const controller = new AbortController();
  const done = runRealStream(request_, callbacks, controller.signal);
  return { done, cancel: () => controller.abort() };
}

async function runRealStream(
  request_: ChatRequest,
  callbacks: ChatStreamCallbacks,
  signal: AbortSignal,
): Promise<ChatFinal> {
  const { SseParser, StreamIncompleteError } = await import("./sse");

  let response: Response;
  try {
    response = await fetch(url("/chat/stream"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(request_),
      signal,
    });
  } catch (cause) {
    if (signal.aborted) throw new ApiError(0, "Request cancelled.");
    const reason = cause instanceof Error ? cause.message : String(cause);
    throw new ApiError(0, `Cannot reach the backend at ${API_URL}. ${reason}`);
  }

  if (!response.ok) throw await toApiError(response);
  if (!response.body) {
    throw new ApiError(0, "The backend returned no response body to stream.");
  }

  const parser = new SseParser();
  const reader = response.body.getReader();
  const decoder = new TextDecoder();

  let final: ChatFinal | null = null;
  try {
    for (;;) {
      const { done: finished, value } = await reader.read();
      if (finished) break;
      // `stream: true` keeps a multi-byte character split across two reads
      // from being decoded into replacement characters mid-answer.
      for (const event of parser.push(decoder.decode(value, { stream: true }))) {
        if (event.type === "token") callbacks.onToken(event.text);
        else if (event.type === "error") callbacks.onError(new ApiError(500, event.payload.detail));
        else if (event.type === "done") final = event.payload;
      }
    }
    for (const event of parser.flush()) {
      if (event.type === "token") callbacks.onToken(event.text);
      else if (event.type === "done") final = event.payload;
    }
  } finally {
    reader.releaseLock();
  }

  // A stream that ends with tokens but no `done` has lost the authoritative
  // text and the citations. Reported, not silently accepted.
  if (!final) {
    throw new StreamIncompleteError(
      "The stream ended before the answer was finalised.",
    );
  }
  return final;
}

const MOCK_TOKEN_MS = 18;

function mockStream(
  request_: ChatRequest,
  callbacks: ChatStreamCallbacks,
): ChatStreamHandle {
  let cancelled = false;
  let timer: ReturnType<typeof setTimeout> | undefined;

  let resolveDone!: (value: ChatFinal) => void;
  let rejectDone!: (reason: Error) => void;
  const done = new Promise<ChatFinal>((resolve, reject) => {
    resolveDone = resolve;
    rejectDone = reject;
  });

  // The fixtures are already in the bundle in mock mode. Awaited rather than
  // cached in a module-level global: a synchronous cache would race the first
  // call, since the import may not have resolved yet.
  void (async () => {
    const { mockAnswerFor, tokenizeAnswer } = await import("./mock/fixtures");
    if (cancelled) return;

    const answer = mockAnswerFor(request_.query);

    // The refusal path must be checked BEFORE any token is emitted, because the
    // real evidence gate declines before the model is ever called - it sends
    // `done` only, with no `token` events at all. Streaming the refusal text
    // first would make the UI's no-token handling untested and would imply the
    // model generated an answer it never generated.
    if (!answer.has_sufficient_evidence) {
      timer = setTimeout(() => resolveDone(answer), MOCK_TOKEN_MS);
      return;
    }

    const tokens = tokenizeAnswer(answer.answer);
    let index = 0;

    const step = () => {
      if (cancelled) return;
      if (index < tokens.length) {
        callbacks.onToken(tokens[index]);
        index += 1;
        timer = setTimeout(step, MOCK_TOKEN_MS);
        return;
      }
      resolveDone(answer);
    };

    timer = setTimeout(step, MOCK_TOKEN_MS);
  })();

  return {
    done,
    cancel: () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
      rejectDone(new ApiError(0, "Request cancelled."));
    },
  };
}
