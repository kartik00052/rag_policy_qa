/**
 * Chat contract (PROJECT.md Section 6).
 *
 * `POST /api/v1/chat/stream` is SSE. Its `done` event carries the full answer
 * plus its citations; `token` events carry fragments of the same text. The
 * frontend therefore holds two views of one answer: the streamed partial, and
 * the authoritative final text from `done`.
 *
 * Note on the backend's validate-then-replay design: tokens are only emitted
 * after the generated answer has passed validation, so a `token` stream here
 * never has to be retracted. A refusal or a validation failure produces no
 * `token` events at all - only `done`.
 */

import type { Citation } from "./citation";

export type MessageRole = "user" | "assistant";

/** A message in the thread. The streaming partial is one of these. */
export interface Message {
  id: string;
  role: MessageRole;
  content: string;
  createdAt: string;
  citations: Citation[];
  /**
   * False when the backend declined to answer from the retrieved evidence.
   * Drives the Section 11.3 insufficient-evidence styling. Distinct from an
   * `error` message: this is a successful, deliberate answer, not a failure.
   */
  hasSufficientEvidence: boolean;
  /** True while tokens are still arriving for this message. */
  isStreaming?: boolean;
  /** Set on a message that reports a transport/provider failure. */
  error?: string;
}

/** Payload of the `done` event. */
export interface ChatFinal {
  answer: string;
  has_sufficient_evidence: boolean;
  citations: Citation[];
}

/** Payload of the `error` event. */
export interface ChatError {
  detail: string;
}

/** Body of `POST /api/v1/chat/stream`. */
export interface ChatRequest {
  query: string;
  conversation_id?: string;
  document_ids?: string[];
}

/** Summary row from `GET /api/v1/conversations`. */
export interface ConversationSummary {
  id: string;
  title: string;
  created_at: string;
}

/**
 * One turn of stored history, from `GET /api/v1/conversations/{id}`.
 * The backend persists citations per assistant message, so a restored
 * conversation carries real, re-clickable evidence.
 */
export interface StoredMessage {
  id: string;
  role: MessageRole;
  content: string;
  created_at: string;
  citations: Citation[];
  has_sufficient_evidence?: boolean;
}

export interface ConversationDetail {
  id: string;
  title: string;
  created_at: string;
  messages: StoredMessage[];
}
