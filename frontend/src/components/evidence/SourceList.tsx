/**
 * Citation list (PROJECT.md Section 11.4, list view).
 *
 * Shown when an answer cited more than one block. Selecting an entry switches
 * to the expanded passage view rather than closing and reopening the panel.
 */

import { ChevronRight } from "lucide-react";
import type { Citation } from "@/types/citation";
import { useEvidenceStore } from "@/stores/evidenceStore";

export function SourceList({ citations }: { citations: Citation[] }) {
  const select = useEvidenceStore((state) => state.select);
  const activeCitation = useEvidenceStore((state) => state.activeCitation);

  return (
    <ul className="divide-y divide-border">
      {citations.map((citation, index) => {
        const active = activeCitation?.id === citation.id;
        return (
          <li key={citation.id}>
            <button
              type="button"
              onClick={() => select(citation)}
              className={[
                "flex w-full items-center gap-2 px-3 py-2.5 text-left transition-colors",
                active ? "bg-highlight" : "hover:bg-surface",
              ].join(" ")}
            >
              <span className="flex-1">
                <span className="block text-[11px] uppercase tracking-[0.08em] text-text-muted">
                  Source #{index + 1}
                </span>
                <span className="mt-0.5 block truncate text-[13px] font-medium text-text-primary">
                  {citation.document_name}
                </span>
                <span className="mt-0.5 block text-[12px] text-text-muted">
                  {citation.page === null
                    ? "No page numbering"
                    : `Page ${citation.page}`}
                  {citation.section ? ` · ${citation.section}` : ""}
                </span>
              </span>
              <ChevronRight size={14} className="shrink-0 text-text-muted" aria-hidden />
            </button>
          </li>
        );
      })}
    </ul>
  );
}
