/**
 * Highlighted passage (PROJECT.md Section 11.4).
 *
 * Fetches the full page text from `GET /api/v1/documents/{id}/pages/{page}`
 * (when page is available) and highlights `citation.matched_text` in its true
 * surrounding context using the `--highlight` token.
 *
 * For flow formats (DOCX/XLSX/CSV) without page numbering, falls back gracefully
 * to rendering the cited blockquote directly.
 */

import { useQuery } from "@tanstack/react-query";
import { getDocumentPage } from "@/lib/api";
import type { Citation } from "@/types/citation";

function renderHighlightedPage(fullText: string, targetText: string) {
  if (!targetText || !fullText) {
    return <span className="whitespace-pre-wrap">{fullText}</span>;
  }

  // Exact match first
  let index = fullText.indexOf(targetText);

  // Case-insensitive fallback
  if (index === -1) {
    index = fullText.toLowerCase().indexOf(targetText.toLowerCase());
  }

  // Whitespace-normalized regex fallback
  if (index === -1) {
    try {
      const escaped = targetText
        .replace(/[.*+?^${}()|[\]\\]/g, "\\$&")
        .replace(/\s+/g, "\\s+");
      const match = new RegExp(escaped, "i").exec(fullText);
      if (match) {
        index = match.index;
        targetText = match[0];
      }
    } catch {
      // Ignore regex compile errors on unusual text
    }
  }

  if (index === -1) {
    return (
      <div className="space-y-3">
        <blockquote className="rounded-md border-l-2 border-accent bg-surface px-3 py-2">
          <p className="text-[13px] leading-relaxed text-text-primary">
            <mark className="rounded-sm bg-highlight px-0.5 text-text-primary font-medium">
              {targetText}
            </mark>
          </p>
        </blockquote>
        <div className="border-t border-border pt-2 text-[12px] text-text-muted whitespace-pre-wrap">
          {fullText}
        </div>
      </div>
    );
  }

  const before = fullText.slice(0, index);
  const matched = fullText.slice(index, index + targetText.length);
  const after = fullText.slice(index + targetText.length);

  return (
    <div className="whitespace-pre-wrap text-[13px] leading-relaxed text-text-primary">
      <span>{before}</span>
      <mark className="rounded-sm bg-highlight px-0.5 text-text-primary font-medium">
        {matched}
      </mark>
      <span>{after}</span>
    </div>
  );
}

export function HighlightedPassage({ citation }: { citation: Citation }) {
  const text = citation.matched_text?.trim();
  const hasPage = citation.page !== null && Boolean(citation.document_id);

  const { data: pageData, isLoading } = useQuery({
    queryKey: ["document-page", citation.document_id, citation.page],
    queryFn: () => getDocumentPage(citation.document_id, citation.page!),
    enabled: hasPage,
  });

  return (
    <div className="space-y-3">
      {/* Wireframe header (PROJECT.md Section 11.4) */}
      <div className="border-b border-border pb-2.5">
        <span className="block text-[10px] font-semibold uppercase tracking-[0.14em] text-text-muted">
          Document View
        </span>
        <h3 className="mt-0.5 truncate font-display text-[15px] font-semibold text-text-primary">
          {citation.document_name}
        </h3>
        <div className="mt-1 flex items-center gap-1.5 text-[12px] text-text-muted">
          <span>{citation.page === null ? "No page numbering" : `Page ${citation.page}`}</span>
          {citation.section ? <span>· {citation.section}</span> : null}
        </div>
      </div>

      {/* Main passage/page view */}
      {hasPage && isLoading ? (
        <div className="space-y-2 py-3 animate-pulse" aria-label="Loading page context">
          <div className="h-4 w-3/4 rounded bg-surface" />
          <div className="h-4 w-full rounded bg-surface" />
          <div className="h-4 w-5/6 rounded bg-surface" />
        </div>
      ) : hasPage && pageData?.text ? (
        <div className="rounded-md border border-border bg-surface/50 p-3 max-h-[420px] overflow-y-auto">
          {renderHighlightedPage(pageData.text, text || "")}
        </div>
      ) : text ? (
        <blockquote className="rounded-md border-l-2 border-accent bg-surface px-3 py-2">
          <p className="text-[13px] leading-relaxed text-text-primary">
            <mark className="rounded-sm bg-highlight px-0.5 text-text-primary">
              {text}
            </mark>
          </p>
        </blockquote>
      ) : (
        <p className="text-[13px] text-warning">
          This citation carries no matched text.
        </p>
      )}

      <p className="border-t border-border pt-2 text-[11px] leading-snug text-text-muted">
        Relevance {citation.relevance.toFixed(2)} — the cross-encoder&rsquo;s
        probability that this passage addresses the question, not a text
        similarity score.
      </p>
    </div>
  );
}

