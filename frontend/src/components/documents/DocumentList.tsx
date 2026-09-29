/**
 * Document list (PROJECT.md Section 11.2).
 *
 * All three documented sidebar states live here: the populated list, the
 * in-flight processing row with a progress fill, and the empty state. The
 * failed row is a fourth real state, not a variant of the progress row - the
 * document has stopped changing and needs a visible reason.
 */

import { motion } from "framer-motion";
import { FileText } from "lucide-react";
import type { DocumentSummary } from "@/types/document";
import { PIPELINE_STAGES, isProcessing } from "@/types/document";
import { DocumentStatusBadge } from "./DocumentStatusBadge";
import { UploadDropzone } from "./UploadDropzone";

/** Section 11.2 progress fill, driven by position in the real stage list. */
function stageProgress(status: DocumentSummary["status"]): number {
  if (status === "ready") return 1;
  if (status === "failed") return 1;
  const index = PIPELINE_STAGES.indexOf(status);
  if (index < 0) return 0;
  return index / (PIPELINE_STAGES.length - 1);
}

export function DocumentList({
  documents,
  isLoading,
  isError,
  errorMessage,
  onRetry,
}: {
  documents: DocumentSummary[];
  isLoading: boolean;
  isError: boolean;
  errorMessage?: string;
  onRetry: () => void;
}) {
  if (isError) {
    return (
      <div className="px-1 py-2">
        <p className="text-[13px] text-warning">
          Could not load documents.
        </p>
        <p className="mt-1 text-[12px] leading-snug text-text-muted">
          {errorMessage ?? "The backend did not respond."}
        </p>
        <button
          type="button"
          onClick={onRetry}
          className="mt-2 rounded-md border border-border px-2 py-1 text-[12px] text-text-primary hover:bg-surface-elevated"
        >
          Retry
        </button>
      </div>
    );
  }

  if (isLoading) {
    return (
      <div className="space-y-2 px-1 py-1" aria-busy="true">
        {[0, 1, 2].map((index) => (
          <div key={index} className="space-y-1.5">
            <div className="h-3 w-3/4 rounded bg-surface-elevated" />
            <div className="h-2 w-1/3 rounded bg-surface-elevated" />
          </div>
        ))}
      </div>
    );
  }

  if (!documents.length) {
    // Section 11.2 empty state: real copy, plus the upload affordance rather
    // than a bare "No data" label.
    return (
      <div className="px-1 py-3">
        <p className="text-[13px] leading-relaxed text-text-primary">
          No documents yet.
        </p>
        <p className="mt-1 text-[13px] leading-relaxed text-text-muted">
          Upload a policy to start asking questions.
        </p>
        <div className="mt-3">
          <UploadDropzone compact />
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-0.5">
      {documents.map((document) => {
        const failed = document.status === "failed";
        const processing = isProcessing(document.status);

        return (
          <div
            key={document.id}
            className="rounded-md px-1.5 py-1.5"
            title={document.filename}
          >
            <div className="flex items-baseline justify-between gap-2">
              <span className="flex min-w-0 items-center gap-1.5">
                <FileText
                  size={13}
                  strokeWidth={1.75}
                  className="shrink-0 text-text-muted"
                  aria-hidden
                />
                <span className="truncate text-[13px] text-text-primary">
                  {document.filename}
                </span>
              </span>
              <span className="shrink-0">
                <DocumentStatusBadge status={document.status} />
              </span>
            </div>

            {processing ? (
              <div className="mt-1.5 pl-[19px]">
                {/*
                  Section 9.5 lists upload-progress transitions as one of the
                  named functional animations, so this width change is
                  animated. Everything else in the sidebar is static.
                */}
                <div className="h-0.5 w-full overflow-hidden rounded-full bg-border">
                  <motion.div
                    className="h-full bg-accent"
                    initial={false}
                    animate={{ width: `${Math.round(stageProgress(document.status) * 100)}%` }}
                    transition={{ duration: 0.35, ease: "easeOut" }}
                  />
                </div>
              </div>
            ) : null}

            {failed ? (
              <p className="mt-1 pl-[19px] text-[12px] leading-snug text-warning">
                Ingestion failed. This document cannot be searched or cited.
                Re-upload the file to try again.
              </p>
            ) : null}

            {document.status === "ready" && document.chunk_count > 0 ? (
              <p className="mt-0.5 pl-[19px] text-[12px] text-text-muted">
                {document.chunk_count} chunks indexed
              </p>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}
