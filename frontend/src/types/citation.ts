/**
 * Citation contract, exactly as implemented by the backend (PROJECT.md
 * Section 6 / `app/schemas/chat.py`).
 *
 * Kept in its own module because a citation is a first-class object in this
 * app: `evidenceStore` holds one, a chip in the chat thread renders one, and
 * the Evidence panel displays one. It is not a chat-message detail, so it does
 * not belong in `types/chat.ts`.
 */
export interface Citation {
  /** Per-answer id ("c1", "c2", ...). Assigned in order of first use. */
  id: string;
  document_id: string;
  document_name: string;
  /** Null for flow formats (DOCX/XLSX/CSV) that carry no page numbers. */
  page: number | null;
  /** Null when the chunk has no heading. */
  section: string | null;
  /**
   * Required by Section 6, and load-bearing: the backend asserts it is a
   * literal substring of the source chunk, because the frontend highlights this
   * exact string. Phase 7 renders it directly as the highlighted span.
   */
  matched_text: string;
  /**
   * Sigmoid of the raw cross-encoder logit, NOT a cosine similarity. Documented
   * deviation from Section 6's `0.91` example - see STATUS.md section 7. Treat
   * it as a relevance probability, never as a vector distance.
   */
  relevance: number;
}

/** "Travel Policy.pdf · p.43" — the chip label. Handles the null-page case. */
export function citationLabel(citation: Citation): string {
  const page = citation.page === null ? "no page" : `p.${citation.page}`;
  return `${citation.document_name} · ${page}`;
}
