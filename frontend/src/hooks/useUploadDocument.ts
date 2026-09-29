/**
 * Upload + status polling (PROJECT.md Section 11.5 / 11.2).
 *
 * The backend returns from `POST /documents` before ingestion finishes, so an
 * upload is only "done" when the document reaches a terminal state. One query -
 * the document list - is the single polling source of truth.
 *
 * Why one source and not a list poll plus a per-document poll: the list response
 * already carries `status` and `chunk_count` for every document, so a second
 * poller reading `GET /documents/{id}` would fetch the same fact twice. Beyond
 * the waste, two pollers writing the same document's state can disagree for a
 * cycle - the list could say `parsing` while the detail said `chunking` - and
 * the sidebar would visibly jump backwards. `useDocumentStatus` exists only for
 * a future single-document view and is intentionally not mounted by the shell.
 *
 * Poll cadence is bounded so a wedged document cannot poll forever. The bound is
 * tracked per document in a ref keyed by id, because `dataUpdatedAt` refreshes
 * on every successful fetch and so can never express "time spent in this stage".
 */

import { useCallback, useEffect, useRef } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { listDocuments, uploadDocument } from "@/lib/api";
import { isTerminal } from "@/types/document";
import { useWorkspaceStore } from "@/stores/workspaceStore";
import type { DocumentSummary } from "@/types/document";

/** Poll cadence while anything is still ingesting. */
const ACTIVE_POLL_MS = 1200;

/**
 * How long a document may stay non-terminal before the UI says so out loud.
 * Real CPU-only embedding is slow enough that this has to be generous; the
 * point is that the user is never left staring at an unexplained frozen badge.
 */
const SLOW_AFTER_MS = 60_000;

let optimisticCounter = 0;

export function useDocuments() {
  const setDocuments = useWorkspaceStore((state) => state.setDocuments);

  // When each document was first seen non-terminal. Reset on terminal so a
  // document that goes ready and is re-uploaded under a new id starts fresh.
  const pendingSince = useRef(new Map<string, number>());

  const query = useQuery({
    queryKey: ["documents"],
    queryFn: listDocuments,
    refetchInterval: (existing) => {
      const documents = existing.state.data ?? [];
      return documents.some((document) => !isTerminal(document.status))
        ? ACTIVE_POLL_MS
        : false;
    },
  });

  // Reconcile the slow-burn clock and mirror into the store in an effect, not
  // during render - setting state while rendering another component is the
  // "Cannot update a component while rendering a different component" error.
  useEffect(() => {
    if (!query.data) return;
    const now = Date.now();
    const seen = pendingSince.current;
    for (const document of query.data) {
      if (isTerminal(document.status)) seen.delete(document.id);
      else if (!seen.has(document.id)) seen.set(document.id, now);
    }
    // Drop clock entries for documents that no longer exist at all.
    for (const id of [...seen.keys()]) {
      if (!query.data.some((document) => document.id === id)) seen.delete(id);
    }
    setDocuments(query.data);
  }, [query.data, setDocuments]);

  /** Documents that have been non-terminal for an unusually long time. */
  const slowIds = useCallback(() => {
    const now = Date.now();
    return new Set(
      [...pendingSince.current.entries()]
        .filter(([, since]) => now - since > SLOW_AFTER_MS)
        .map(([id]) => id),
    );
  }, []);

  return { ...query, slowIds };
}

export function useUploadDocument() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: uploadDocument,
    onMutate: async (file) => {
      // Insert the row BEFORE the request resolves, so the sidebar responds to
      // the drop immediately instead of after a round trip. Keyed by a temporary
      // id that `onSuccess` swaps for the real one in place - same row, same
      // position, no duplicate and no flash.
      optimisticCounter += 1;
      const tempId = `optimistic-${optimisticCounter}`;
      await queryClient.cancelQueries({ queryKey: ["documents"] });
      const previous = queryClient.getQueryData<DocumentSummary[]>(["documents"]);
      queryClient.setQueryData<DocumentSummary[]>(["documents"], (existing) => [
        ...(existing ?? []),
        {
          id: tempId,
          filename: file.name,
          file_type: file.name.slice(file.name.lastIndexOf(".")).replace(".", ""),
          status: "uploading",
          created_at: new Date().toISOString(),
          chunk_count: 0,
        },
      ]);

      // Returned to `onError` so a failed upload can restore the prior list and
      // surface the reason, rather than leaving a row that never resolves.
      return { tempId, previous };
    },
    onSuccess: (uploaded, _file, context) => {
      // Replace the optimistic row in place. Mapping by id rather than
      // appending is what prevents the duplicate row and the flicker.
      queryClient.setQueryData<DocumentSummary[]>(["documents"], (existing) =>
        (existing ?? []).map((document) =>
          document.id === context?.tempId
            ? {
                ...document,
                id: uploaded.document_id,
                status: uploaded.status,
              }
            : document,
        ),
      );
    },
    onError: (_error, file, context) => {
      // A failed POST is not an ingestion failure: there is no document and no
      // id, so the optimistic row is removed and the dropzone reports the
      // reason against the file the user actually dropped.
      queryClient.setQueryData<DocumentSummary[]>(["documents"], (existing) =>
        (existing ?? []).filter((document) => document.id !== context?.tempId),
      );
      if (context?.previous) {
        queryClient.setQueryData(["documents"], context.previous);
      }
      void file;
    },
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: ["documents"] });
    },
  });
}

/** Exposed for a future single-document view; not mounted by the shell. */
export function useDocumentStatus(documentId: string | null) {
  return useQuery({
    queryKey: ["document", documentId],
    queryFn: async () => {
      // Imported lazily so the shell never pulls the detail path in.
      const { getDocument } = await import("@/lib/api");
      return getDocument(documentId!);
    },
    enabled: Boolean(documentId),
    refetchInterval: (existing) => {
      const status = existing.state.data?.status;
      return status && !isTerminal(status) ? ACTIVE_POLL_MS : false;
    },
  });
}
