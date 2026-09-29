/**
 * Workspace sidebar (PROJECT.md Section 11.2).
 *
 * Workspaces are cosmetic in V1. The backend has no workspace concept - there
 * is no workspace column on `documents` and `GET /api/v1/documents` returns
 * everything - so these three labels are drawn because Section 11.2 specifies
 * them, while the document list below shows every document regardless of which
 * one is selected. Selecting a workspace is therefore a no-op on the data, and
 * the active marker exists only to match the specified layout.
 *
 * Making this real needs a workspaces table, a document relation and backend
 * filtering, all of which are out of V1 scope.
 */

import { WORKSPACES } from "@/stores/workspaceStore";
import { useWorkspaceStore } from "@/stores/workspaceStore";
import { DocumentList } from "@/components/documents/DocumentList";
import { UploadDropzone } from "@/components/documents/UploadDropzone";

function SectionLabel({ children }: { children: string }) {
  return (
    <h2 className="px-1.5 pb-1 text-[11px] font-medium uppercase tracking-[0.08em] text-text-muted">
      {children}
    </h2>
  );
}

export function WorkspaceSidebar({
  documents,
  isLoading,
  isError,
  errorMessage,
  onRetry,
}: {
  documents: ReturnType<typeof useWorkspaceStore.getState>["documents"];
  isLoading: boolean;
  isError: boolean;
  errorMessage?: string;
  onRetry: () => void;
}) {
  const activeWorkspaceId = useWorkspaceStore((state) => state.activeWorkspaceId);
  const setActiveWorkspace = useWorkspaceStore((state) => state.setActiveWorkspace);

  return (
    <aside className="flex w-60 shrink-0 flex-col border-r border-border bg-surface">
      <nav className="shrink-0 px-1.5 pt-3" aria-label="Workspaces">
        <SectionLabel>Workspaces</SectionLabel>
        <ul className="space-y-px">
          {WORKSPACES.map((workspace) => {
            const active = workspace === activeWorkspaceId;
            return (
              <li key={workspace}>
                <button
                  type="button"
                  onClick={() => setActiveWorkspace(workspace)}
                  aria-current={active ? "true" : undefined}
                  className={[
                    "flex w-full items-center gap-2 rounded-md px-1.5 py-1 text-left text-[13px]",
                    active ? "text-accent" : "text-text-muted hover:text-text-primary",
                  ].join(" ")}
                >
                  {/* Section 11.2: accent dot + accent text on the active row. */}
                  <span
                    className={[
                      "h-1.5 w-1.5 shrink-0 rounded-full",
                      active ? "bg-accent" : "border border-text-muted",
                    ].join(" ")}
                    aria-hidden
                  />
                  {workspace}
                </button>
              </li>
            );
          })}
        </ul>
      </nav>

      <div className="mt-4 flex min-h-0 flex-1 flex-col border-t border-border px-1.5 pt-3">
        <div className="flex min-h-0 flex-1 flex-col overflow-y-auto">
          <SectionLabel>Documents</SectionLabel>
          <DocumentList
            documents={documents}
            isLoading={isLoading}
            isError={isError}
            errorMessage={errorMessage}
            onRetry={onRetry}
          />
        </div>

        {/* Section 11.5: always-available upload affordance, not just when the
            sidebar is empty. */}
        <div className="shrink-0 py-2">
          <UploadDropzone compact />
        </div>
      </div>
    </aside>
  );
}
