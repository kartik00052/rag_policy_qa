/**
 * Upload + status polling (PROJECT.md Section 11.5 / 11.2).
 *
 * The backend returns from `POST /documents` before ingestion finishes, so an
 * upload is only "done" when the document reaches a terminal state. This polls
 * `GET /documents/{id}` until `ready` or `failed`, then invalidates the list so
 * the sidebar shows the new row and its final status.
 *
 * The list query also polls while any document is still in flight - that is how
 * the sidebar's processing row advances through Parsing → Chunking → Embedding
 * → Indexing → Ready, which Section 11.2 requires by real stage name.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { getDocument, listDocuments, uploadDocument } from "@/lib/api";
import { isTerminal } from "@/types/document";
import { useWorkspaceStore } from "@/stores/workspaceStore";

/** Poll cadence while anything is still ingesting. */
const ACTIVE_POLL_MS = 1200;
/** Bounded so a wedged document surfaces as an error rather than polling forever. */
const MAX_POLL_MS = 180_000;

export function useDocuments() {
  const setDocuments = useWorkspaceStore((state) => state.setDocuments);

  const query = useQuery({
    queryKey: ["documents"],
    queryFn: listDocuments,
    refetchInterval: (existing) => {
      const documents = existing.state.data ?? [];
      const pending = documents.some((document) => !isTerminal(document.status));
      return pending ? ACTIVE_POLL_MS : false;
    },
  });

  if (query.data) setDocuments(query.data);

  return query;
}

export function useUploadDocument() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: uploadDocument,
    onSuccess: (uploaded) => {
      // Show the row immediately rather than waiting for the list refetch, so
      // Section 11.5's "immediately shows the processing state" holds even
      // before the first poll returns.
      queryClient.setQueryData(
        ["documents"],
        (existing: Awaited<ReturnType<typeof listDocuments>> | undefined) => [
          ...(existing ?? []),
          {
            id: uploaded.document_id,
            filename: "Uploading…",
            file_type: "",
            status: uploaded.status,
            created_at: new Date().toISOString(),
            chunk_count: 0,
          },
        ],
      );
      void queryClient.invalidateQueries({ queryKey: ["documents"] });
      void queryClient.invalidateQueries({ queryKey: ["document", uploaded.document_id] });
    },
  });
}

/**
 * Per-document status poller. Exposed for the detail path; the sidebar relies on
 * the list query's own polling, since it needs every document's stage at once.
 */
export function useDocumentStatus(documentId: string | null) {
  return useQuery({
    queryKey: ["document", documentId],
    queryFn: () => getDocument(documentId!),
    enabled: Boolean(documentId),
    refetchInterval: (existing) => {
      const status = existing.state.data?.status;
      // Bounded by wall clock as well as by state, so a document stuck in a
      // non-terminal state reports a visible error instead of polling silently.
      const startedAt = existing.state.dataUpdatedAt;
      if (startedAt && Date.now() - startedAt > MAX_POLL_MS) return false;
      return status && !isTerminal(status) ? ACTIVE_POLL_MS : false;
    },
  });
}
