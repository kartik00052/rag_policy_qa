/**
 * Evidence store (PROJECT.md Section 8).
 *
 * This is the single owner of the third pane. Section 8 is explicit that
 * clicking a citation chip *anywhere* calls `openCitation()` and "nothing else
 * needs to know about the panel" - so the panel takes its whole input from here
 * and no component holds citation state of its own.
 */

import { create } from "zustand";
import type { Citation } from "@/types/citation";

/**
 * A citation plus its siblings.
 *
 * `siblings` exists so the panel can offer "Source #1 / Source #2" navigation
 * when an answer cited more than one block, which Section 11.4 requires. The
 * backend sends a flat array per answer and nothing links them, so the
 * citations of the message a chip belongs to are captured at click time and
 * carried alongside the selected one. Without this the panel could show one
 * passage but could not list the alternatives.
 */
export interface EvidenceSelection {
  citation: Citation;
  siblings: Citation[];
}

interface EvidenceStore {
  isOpen: boolean;
  activeCitation: Citation | null;
  /** Citations from the same answer, for the multi-source list view. */
  siblings: Citation[];
  /** True once a specific source is chosen, i.e. the expanded passage view. */
  isExpanded: boolean;

  openCitation: (citation: Citation, siblings?: Citation[]) => void;
  /** Open the panel at the list level, without expanding a passage. */
  openList: (citations: Citation[]) => void;
  /** Expand a source that is already open in the panel. */
  select: (citation: Citation) => void;
  /** Step back from the expanded view to the source list. */
  backToList: () => void;
  close: () => void;
}

export const useEvidenceStore = create<EvidenceStore>((set, get) => ({
  isOpen: false,
  activeCitation: null,
  siblings: [],
  isExpanded: false,

  openCitation: (citation, siblings = [citation]) => {
    set({
      isOpen: true,
      activeCitation: citation,
      siblings: siblings.length ? siblings : [citation],
      isExpanded: true,
    });
  },

  openList: (citations) => {
    if (!citations.length) return;
    set({ isOpen: true, activeCitation: null, siblings: citations, isExpanded: false });
  },

  select: (citation) => {
    const { siblings } = get();
    set({
      isOpen: true,
      activeCitation: citation,
      // Keep the list the panel is navigating within stable, so switching
      // sources cross-fades the passage instead of reshuffling the list.
      siblings: siblings.length ? siblings : [citation],
      isExpanded: true,
    });
  },

  backToList: () => set({ isExpanded: false }),

  close: () =>
    set({ isOpen: false, activeCitation: null, siblings: [], isExpanded: false }),
}));

/** Narrowing selector: the selected citation, or null when at list level. */
export const selectActiveCitation = (state: EvidenceStore) => state.activeCitation;
