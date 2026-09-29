/**
 * Highlighted passage (PROJECT.md Section 11.4).
 *
 * Scope decision, stated explicitly: `GET /api/v1/documents/{id}/pages/{page}`
 * does not exist on the backend, so there is no page text or bounding box to
 * fetch. `citation.matched_text` *is* the literal cited substring - the backend
 * asserts it is a true substring of the source chunk before it ever reaches the
 * API - so it is rendered directly as the highlighted passage rather than
 * pretending to be a window into a page we cannot load.
 *
 * Section 11.4 draws the highlight as literal `▓` blocks and requires it be
 * implemented as a real `<mark>`-style span using `--highlight`, which is what
 * this does.
 *
 * TODO(evidence-viewer): when `GET /api/v1/documents/{id}/pages/{page}` exists,
 * fetch the full page text here and highlight `matched_text` at its true
 * position within it, which turns this from "the cited sentence" into "the
 * cited sentence in its surrounding context".
 */

import type { Citation } from "@/types/citation";

export function HighlightedPassage({ citation }: { citation: Citation }) {
  const text = citation.matched_text?.trim();

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-baseline gap-x-2 text-[13px] text-text-muted">
        <span className="font-medium text-text-primary">{citation.document_name}</span>
        {/* Page is null for flow formats (DOCX/XLSX/CSV), which carry no page
            numbers - said rather than rendered as a broken "p.null". */}
        <span>{citation.page === null ? "No page numbering" : `Page ${citation.page}`}</span>
        {citation.section ? <span>· {citation.section}</span> : null}
      </div>

      {text ? (
        <blockquote className="rounded-md border-l-2 border-accent bg-surface px-3 py-2">
          <p className="text-[13px] leading-relaxed text-text-primary">
            <mark className="rounded-sm bg-highlight px-0.5 text-text-primary">
              {text}
            </mark>
          </p>
        </blockquote>
      ) : (
        // Defensive: the backend marks `matched_text` as required, so this
        // should not render. Better a visible gap than a silent blank panel.
        <p className="text-[13px] text-warning">
          This citation carries no matched text.
        </p>
      )}

      <p className="text-[11px] leading-snug text-text-muted">
        Relevance {citation.relevance.toFixed(2)} — the cross-encoder&rsquo;s
        probability that this passage addresses the question, not a text
        similarity score.
      </p>
    </div>
  );
}
