/**
 * Dev-only fixtures (Phase 2).
 *
 * These are captured from real backend output rather than invented, so mock
 * mode exercises the same shapes the real API returns:
 *
 *  - `MULTI_CITATION_ANSWER` mirrors a real expense-claim answer, whose text
 *    was verified across 3 runs of `verify_generation`.
 *  - `NO_EVIDENCE_ANSWER` is the real gate-decline payload for "What is the
 *    nightly accommodation cap in New York?" - a documented, reproducible
 *    false negative in the evidence gate, which makes it the honest fixture for
 *    the insufficient-evidence UI state rather than a convenient one.
 *  - `match_text` values are the real `matched_text` excerpts the backend
 *    returns. The backend asserts each is a literal substring of its chunk, so
 *    the highlight the panel renders is genuinely highlightable text.
 *
 * Nothing here is imported unless `VITE_MOCK_API` is set; see `lib/api.ts`.
 */

import type { ChatFinal } from "@/types/chat";
import type { Citation } from "@/types/citation";
import type {
  ConversationDetail,
  ConversationSummary,
} from "@/types/chat";
import type {
  DocumentDetail,
  DocumentSummary,
} from "@/types/document";

export const TRAVEL_POLICY_ID = "73d7e9b1-ebd9-47a3-b5d5-4dd91702c498";
export const HR_HANDBOOK_ID = "1b6f5a20-2c31-4a2e-9b0f-2f5a9c7e4411";
export const EXPENSE_PROCEDURE_ID = "9c2d4e11-77aa-4c3f-8e21-5b6c7d8e9f02";

/**
 * Two citations from one answer, for the Section 11.4 multi-source list view.
 * Both carry real page and section metadata.
 */
export const MULTI_CITATIONS: Citation[] = [
  {
    id: "c1",
    document_id: TRAVEL_POLICY_ID,
    document_name: "acme_travel_policy.pdf",
    page: 1,
    section: "6.2 Reimbursement Limits",
    matched_text:
      "reimbursed at a flat daily allowance of 75 USD for domestic travel and 100 USD for international travel. Itemised meal receipts are not required.",
    relevance: 0.9902,
  },
  {
    id: "c2",
    document_id: TRAVEL_POLICY_ID,
    document_name: "acme_travel_policy.pdf",
    page: 2,
    section: "6.2 Reimbursement Limits",
    matched_text:
      "Expense claims must be submitted within 30 days of completing the trip. Claims submitted after this period are only reimbursed at the discretion of the Head of Finance.",
    relevance: 0.9715,
  },
];

/** One citation, for the single-source case. */
export const SINGLE_CITATION: Citation[] = [
  {
    id: "c1",
    document_id: TRAVEL_POLICY_ID,
    document_name: "acme_travel_policy.pdf",
    page: 1,
    section: "4. Travel Approval",
    matched_text:
      "Travel requests must be submitted at least 5 working days before departure and require manager approval before booking.",
    relevance: 0.9816,
  },
];

/**
 * A DOCX-sourced citation: `page` is null. Flow formats carry no page numbers,
 * so this is the fixture for rendering that case without inventing a page.
 */
export const NO_PAGE_CITATION: Citation[] = [
  {
    id: "c1",
    document_id: HR_HANDBOOK_ID,
    document_name: "acme_hr_handbook.docx",
    page: null,
    section: "7.2 Leave",
    matched_text:
      "Full-time employees accrue 1.75 days of annual leave per completed calendar month, with an annual cap of 25 days.",
    relevance: 0.9881,
  },
];

export const MULTI_CITATION_ANSWER: ChatFinal = {
  answer:
    "You have to submit an expense claim within 30 days of completing the trip. Claims submitted after this period are only reimbursed at the discretion of the Head of Finance, and receipts are mandatory for all claims.",
  has_sufficient_evidence: true,
  citations: MULTI_CITATIONS,
};

export const SINGLE_CITATION_ANSWER: ChatFinal = {
  answer:
    "Manager approval is required for all travel, and requests must be submitted at least 5 working days before travel.",
  has_sufficient_evidence: true,
  citations: SINGLE_CITATION,
};

/**
 * The real refusal payload. `has_sufficient_evidence` is false and the citation
 * list is empty, and critically the backend emits NO `token` events for this -
 * the evidence gate closes before the model is ever called. The UI must render
 * this as a styled answer, not as an error and not as an empty bubble.
 */
export const NO_EVIDENCE_ANSWER: ChatFinal = {
  answer:
    "I couldn't find this in the documents. Try rephrasing your question, or check that a policy covering this topic has been uploaded.",
  has_sufficient_evidence: false,
  citations: [],
};

/** A failed generation, for the transport/provider error state. */
export const GENERATION_ERROR: { detail: string } = {
  detail:
    "The answer could not be generated. The model provider did not respond. Please retry.",
};

/**
 * Pre-chunked answer text, so mock streaming appends the same substrings the
 * real backend would have sent. Split on word boundaries rather than mid-word so
 * the streamed rendering matches real token boundaries closely enough to judge
 * the caret and spacing.
 */
export function tokenizeAnswer(answer: string): string[] {
  return answer.match(/\S+\s*/g) ?? [answer];
}

/** Answers keyed by a normalised question, so mock chat is deterministic. */
export const MOCK_ANSWERS: Record<string, ChatFinal> = {
  "how long do i have to submit an expense claim after a trip?":
    MULTI_CITATION_ANSWER,
  "what is the daily meal allowance for an international trip?": {
    answer:
      "The daily meal allowance for an international trip is 100 USD.",
    has_sufficient_evidence: true,
    citations: MULTI_CITATIONS.slice(0, 1),
  },
  "can i book business class for a five hour flight?": {
    answer:
      "No, economy class must be booked for all flights under 6 hours. Business class requires written director sign-off.",
    has_sufficient_evidence: true,
    citations: SINGLE_CITATION,
  },
  "who must approve travel, and how far in advance?": SINGLE_CITATION_ANSWER,
  "how much annual leave do i accrue each month, and what is the cap?": {
    answer:
      "Full-time employees accrue 1.75 days of annual leave per completed calendar month, with an annual cap of 25 days.",
    has_sufficient_evidence: true,
    citations: NO_PAGE_CITATION,
  },
  "what is the nightly accommodation cap in new york?": NO_EVIDENCE_ANSWER,
};

/**
 * Fallback for an unrecognised mock question. Cited and evidence-bearing on
 * purpose: an unknown question must not silently exercise the refusal path,
 * which would make the insufficient-evidence state look reachable by accident.
 */
export const MOCK_FALLBACK_ANSWER: ChatFinal = MULTI_CITATION_ANSWER;

export function mockAnswerFor(query: string): ChatFinal {
  return MOCK_ANSWERS[query.trim().toLowerCase()] ?? MOCK_FALLBACK_ANSWER;
}

/**
 * Document list covering every state the sidebar must render (Section 11.2).
 *
 * The list deliberately spans the whole pipeline rather than just its endpoints:
 * `uploading`, `parsing`, `chunking`, `embedding` and `indexing` are real
 * statuses the backend reports, and Section 11.2 requires each stage to be shown
 * by name instead of a generic spinner. Without all five rows here, the
 * stage-label path is only ever partly exercised, and the sidebar looks correct
 * while still being wrong for four of its five processing states.
 */
export const MOCK_DOCUMENTS: DocumentSummary[] = [
  {
    id: TRAVEL_POLICY_ID,
    filename: "acme_travel_policy.pdf",
    file_type: "pdf",
    status: "ready",
    created_at: "2026-09-27T07:14:26Z",
    chunk_count: 13,
  },
  {
    id: HR_HANDBOOK_ID,
    filename: "acme_hr_handbook.docx",
    file_type: "docx",
    status: "ready",
    created_at: "2026-09-27T07:31:02Z",
    chunk_count: 24,
  },
  {
    id: EXPENSE_PROCEDURE_ID,
    filename: "expense_procedure.xlsx",
    file_type: "xlsx",
    status: "ready",
    created_at: "2026-09-27T08:02:10Z",
    chunk_count: 7,
  },
  // One document parked at each non-terminal stage.
  {
    id: "a1b2c3d4-1111-4a2b-8c3d-4e5f6a7b8c9d",
    filename: "corporate_travel_addendum.pdf",
    file_type: "pdf",
    status: "uploading",
    created_at: "2026-09-27T09:00:00Z",
    chunk_count: 0,
  },
  {
    id: "a1b2c3d4-2222-4a2b-8c3d-4e5f6a7b8c9d",
    filename: "remote_work_policy.pdf",
    file_type: "pdf",
    status: "parsing",
    created_at: "2026-09-27T09:00:20Z",
    chunk_count: 0,
  },
  {
    id: "a1b2c3d4-3333-4a2b-8c3d-4e5f6a7b8c9d",
    filename: "information_security.pdf",
    file_type: "pdf",
    status: "chunking",
    created_at: "2026-09-27T09:00:40Z",
    chunk_count: 0,
  },
  {
    id: "a1b2c3d4-4444-4a2b-8c3d-4e5f6a7b8c9d",
    filename: "procurement_guidelines.pdf",
    file_type: "pdf",
    status: "embedding",
    created_at: "2026-09-27T09:01:00Z",
    chunk_count: 0,
  },
  {
    id: EXPENSE_PROCEDURE_ID + "-indexing",
    filename: "payroll_handbook.pdf",
    file_type: "pdf",
    status: "indexing",
    created_at: "2026-09-27T09:01:20Z",
    chunk_count: 0,
  },
  {
    id: "c4d5e6f7-8899-4a0b-9c1d-2e3f4a5b6c7d",
    filename: "security_addendum.pdf",
    file_type: "pdf",
    status: "failed",
    created_at: "2026-09-27T09:10:15Z",
    chunk_count: 0,
  },
];

export const MOCK_DOCUMENT_DETAIL: DocumentDetail = {
  ...MOCK_DOCUMENTS[0],
  stage_index: 5,
  stage_total: 6,
};

/** The empty-sidebar state (Section 11.2) is exercised by this flag. */
export const EMPTY_DOCUMENTS: DocumentSummary[] = [];

export const MOCK_CONVERSATIONS: ConversationSummary[] = [
  {
    id: "5f1c9e20-7a3b-4d18-9c22-8e5b6a7d9f31",
    title: "Expense claim windows",
    created_at: "2026-09-27T08:12:00Z",
  },
  {
    id: "6a2d0f31-8b4c-4e29-8d33-9f6c7b8e0a42",
    title: "Business class approval",
    created_at: "2026-09-26T14:03:00Z",
  },
];

/**
 * Stored history, used for the reload-restore path. Citations are carried so a
 * restored answer's chips are re-clickable and re-highlightable.
 */
export const MOCK_CONVERSATION_DETAIL: ConversationDetail = {
  id: MOCK_CONVERSATIONS[0].id,
  title: MOCK_CONVERSATIONS[0].title,
  created_at: MOCK_CONVERSATIONS[0].created_at,
  messages: [
    {
      id: "m-1",
      role: "user",
      content: "How long do I have to submit an expense claim after a trip?",
      created_at: "2026-09-27T08:12:04Z",
      citations: [],
    },
    {
      id: "m-2",
      role: "assistant",
      content: MULTI_CITATION_ANSWER.answer,
      created_at: "2026-09-27T08:12:31Z",
      citations: MULTI_CITATIONS,
      has_sufficient_evidence: true,
    },
  ],
};
