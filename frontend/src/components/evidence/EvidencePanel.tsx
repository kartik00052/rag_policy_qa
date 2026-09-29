/**
 * Evidence panel (PROJECT.md Section 11.4).
 *
 * Two behaviours here are load-bearing:
 *
 *  - **Closed means zero width.** Section 11.4 forbids rendering an empty 360px
 *    box with a header and nothing in it. The panel is unmounted when closed, so
 *    it cannot occupy layout space at all.
 *  - **Switching sources cross-fades** rather than closing and reopening
 *    (Section 11.4), which is why the passage content is keyed on the citation
 *    id inside an `AnimatePresence`/`motion.div` pair while the panel frame
 *    itself stays mounted.
 *
 * The panel reads everything from `evidenceStore` and takes no props, because
 * Section 8 makes it the single consumer of citation state.
 */

import { AnimatePresence, motion } from "framer-motion";
import { ArrowLeft, X } from "lucide-react";
import { useEvidenceStore } from "@/stores/evidenceStore";
import { HighlightedPassage } from "./HighlightedPassage";
import { SourceList } from "./SourceList";

const PANEL_WIDTH = 360;

export function EvidencePanel() {
  const isOpen = useEvidenceStore((state) => state.isOpen);
  const isExpanded = useEvidenceStore((state) => state.isExpanded);
  const activeCitation = useEvidenceStore((state) => state.activeCitation);
  const siblings = useEvidenceStore((state) => state.siblings);
  const close = useEvidenceStore((state) => state.close);
  const backToList = useEvidenceStore((state) => state.backToList);

  return (
    <AnimatePresence initial={false}>
      {isOpen ? (
        <motion.aside
          key="evidence-panel"
          initial={{ width: 0, opacity: 0 }}
          animate={{ width: PANEL_WIDTH, opacity: 1 }}
          exit={{ width: 0, opacity: 0 }}
          // Section 11.4: slide in from the right, ~250ms ease-out.
          transition={{ duration: 0.25, ease: "easeOut" }}
          style={{ width: PANEL_WIDTH }}
          className="shrink-0 overflow-hidden border-l border-border bg-surface-elevated"
          aria-label="Evidence"
        >
          <div className="flex h-full w-[360px] flex-col">
            <header className="flex h-12 shrink-0 items-center gap-2 border-b border-border px-3">
              {isExpanded ? (
                <>
                  <button
                    type="button"
                    onClick={backToList}
                    className="inline-flex items-center gap-1 rounded px-1 py-0.5 text-[12px] text-text-muted hover:text-text-primary"
                  >
                    <ArrowLeft size={13} strokeWidth={2} aria-hidden />
                    Back to sources
                  </button>
                </>
              ) : (
                <h2 className="font-display text-[13px] font-medium uppercase tracking-[0.14em] text-text-primary">
                  Evidence
                </h2>
              )}

              <div className="flex-1" />

              <button
                type="button"
                onClick={close}
                aria-label="Close evidence panel"
                className="inline-flex h-7 w-7 items-center justify-center rounded-md text-text-muted hover:bg-surface hover:text-text-primary"
              >
                <X size={15} strokeWidth={2} />
              </button>
            </header>

            <div className="min-h-0 flex-1 overflow-y-auto">
              {/* Cross-fade between sources, keyed on the citation id so
                  switching passages fades content instead of remounting the
                  panel frame. */}
              <AnimatePresence mode="wait" initial={false}>
                <motion.div
                  key={
                    isExpanded && activeCitation
                      ? `expanded-${activeCitation.id}`
                      : `list-${siblings.map((c) => c.id).join("-")}`
                  }
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  exit={{ opacity: 0 }}
                  transition={{ duration: 0.15, ease: "easeOut" }}
                  className="p-3"
                >
                  {isExpanded && activeCitation ? (
                    <HighlightedPassage citation={activeCitation} />
                  ) : (
                    <SourceList citations={siblings} />
                  )}
                </motion.div>
              </AnimatePresence>
            </div>
          </div>
        </motion.aside>
      ) : null}
    </AnimatePresence>
  );
}
