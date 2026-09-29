/**
 * Workspace store (PROJECT.md Section 8).
 *
 * Scoping note, stated explicitly rather than faked: the backend has no
 * workspace concept. `documents` (Section 5) has no workspace column and
 * `GET /api/v1/documents` returns every document regardless. Section 11.2
 * draws HR / Finance / Security in the sidebar, so they are rendered as a
 * single cosmetic grouping over one implicit workspace rather than as a real
 * filter that would silently drop documents. See `WORKSPACES` below.
 *
 * `documents` is held here (as Section 8 specifies) but written by the TanStack
 * Query layer, which owns fetching and polling; this store is the cache the
 * sidebar reads.
 */

import { create } from "zustand";
import type { DocumentSummary } from "@/types/document";

/**
 * V1 is single-tenant with no workspace column anywhere in the schema, so all
 * documents live in one implicit workspace. The three labels exist because
 * Section 11.2 specifies that sidebar grouping. They are presentation only:
 * selecting one filters nothing, because there is nothing to filter on.
 *
 * Making these real would require a `workspaces` table, a document-to-workspace
 * relation, and backend filtering - all out of V1 scope, so deliberately not
 * built.
 */
export const WORKSPACES = ["HR", "Finance", "Security"] as const;

export type WorkspaceId = (typeof WORKSPACES)[number];

export const IMPLICIT_WORKSPACE: WorkspaceId = "HR";

interface WorkspaceStore {
  activeWorkspaceId: string | null;
  documents: DocumentSummary[];
  setActiveWorkspace: (id: string) => void;
  setDocuments: (documents: DocumentSummary[]) => void;
}

export const useWorkspaceStore = create<WorkspaceStore>((set) => ({
  activeWorkspaceId: IMPLICIT_WORKSPACE,
  documents: [],
  setActiveWorkspace: (id) => set({ activeWorkspaceId: id }),
  setDocuments: (documents) => set({ documents }),
}));
