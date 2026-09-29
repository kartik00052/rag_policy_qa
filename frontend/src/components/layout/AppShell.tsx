/**
 * App shell (PROJECT.md Section 11.1).
 *
 * Three panes: fixed 240px sidebar, fluid centre with a 720px reading column,
 * and a 360px Evidence panel that is genuinely absent until a citation is
 * opened. The centre column's max width lives here rather than in the thread so
 * the reading measure is one decision in one place.
 */

import { TopBar } from "./TopBar";
import { WorkspaceSidebar } from "./WorkspaceSidebar";
import { EvidencePanel } from "@/components/evidence/EvidencePanel";
import { MessageThread } from "@/components/chat/MessageThread";
import { useDocuments } from "@/hooks/useUploadDocument";
import { useWorkspaceStore } from "@/stores/workspaceStore";

export function AppShell() {
  const documentsQuery = useDocuments();
  const documents = useWorkspaceStore((state) => state.documents);

  return (
    <div className="flex h-svh flex-col bg-bg text-text-primary">
      <TopBar />

      <div className="flex min-h-0 flex-1">
        <WorkspaceSidebar
          documents={documents}
          isLoading={documentsQuery.isLoading}
          isError={documentsQuery.isError}
          errorMessage={
            documentsQuery.error instanceof Error
              ? documentsQuery.error.message
              : undefined
          }
          onRetry={() => void documentsQuery.refetch()}
        />

        {/*
          Section 11.1 backend-unavailable state: a transport failure gets real
          copy and a retry, not a spinner (Section 9.6).
        */}
        {documentsQuery.isError ? (
          <main className="flex min-w-0 flex-1 items-center justify-center bg-bg px-6">
            <div className="max-w-[420px] text-center">
              <h2 className="font-display text-[15px] font-medium text-text-primary">
                Can&rsquo;t reach the policy service
              </h2>
              <p className="mt-2 text-[13px] leading-relaxed text-text-muted">
                Start the backend, then retry. Documents and answers both need it.
              </p>
              <button
                type="button"
                onClick={() => void documentsQuery.refetch()}
                className="mt-4 rounded-md border border-border px-3 py-1.5 text-[13px] text-text-primary hover:bg-surface"
              >
                Retry
              </button>
            </div>
          </main>
        ) : (
          <main className="flex min-w-0 flex-1 flex-col">
            <MessageThread />
          </main>
        )}

        <EvidencePanel />
      </div>
    </div>
  );
}
