/**
 * Citation chip (PROJECT.md Section 11.3).
 *
 * Rendered inline at the cited position in an answer, styled
 * `bg-surface` / `border` / `text-accent` with a small document icon. Clicking
 * it calls `evidenceStore.openCitation()` with the whole citation object and no
 * extra fetch - Section 8 makes the Evidence panel the only consumer of
 * citation state.
 *
 * The `siblings` prop carries the other citations from the same answer so the
 * panel can offer the multi-source list view from Section 11.4 without any
 * component having to know how the backend flattens them.
 */

import { motion } from "framer-motion";
import { FileText } from "lucide-react";
import type { Citation } from "@/types/citation";
import { citationLabel } from "@/types/citation";
import { useEvidenceStore } from "@/stores/evidenceStore";

export function CitationChip({
  citation,
  siblings,
}: {
  citation: Citation;
  siblings?: Citation[];
}) {
  const openCitation = useEvidenceStore((state) => state.openCitation);
  const label = citationLabel(citation);

  return (
    <motion.button
      type="button"
      onClick={() => openCitation(citation, siblings)}
      title={`${label}${citation.section ? ` · ${citation.section}` : ""}`}
      // Section 9.5: citation chip hover is one of the named functional
      // animations, so it is the only motion this component has.
      whileHover={{ y: -1 }}
      transition={{ duration: 0.12, ease: "easeOut" }}
      className="mx-0.5 inline-flex items-baseline gap-1 rounded border border-border bg-surface px-1.5 py-px align-baseline text-[12px] text-accent hover:border-accent"
    >
      <FileText size={11} strokeWidth={2} className="self-center" aria-hidden />
      {label}
    </motion.button>
  );
}
