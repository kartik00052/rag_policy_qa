/**
 * Drop target for uploads (PROJECT.md Section 11.5).
 *
 * Dashed `--border` outline, `--accent` on drag-over. On drop it hands every
 * dropped file to `useUploadDocument` and shows the processing state in the
 * sidebar - 11.5 specifies no separate modal or full-page uploading screen.
 *
 * Multi-file: every file in the drop is uploaded. Handling only the first (as an
 * earlier revision did) silently discarded the rest, which looked like the drop
 * was ignored when more than one file was selected.
 *
 * Two failure modes are reported differently, because they are different:
 *  - the request itself failing (network error, or the backend's 400 for an
 *    unsupported type) leaves no document and no id, so the message is attached
 *    to the file the user dropped;
 *  - ingestion failing later surfaces as a `failed` badge on the sidebar row.
 *
 * `disabled` is set by the parent while any document is actively ingesting.
 * The backend runs ingestion in-process behind a single converter lock
 * (PROJECT.md Section 2: no worker queue in V1), so a second concurrent upload
 * is accepted by the HTTP layer but its ingestion task blocks until the first
 * parse finishes. Showing a clear reason is more honest than silently accepting
 * a file that will be delayed.
 */

import { useRef, useState } from "react";
import { AlertCircle, X } from "lucide-react";
import { useUploadDocument } from "@/hooks/useUploadDocument";
import { acceptedTypes } from "@/types/document";

export function UploadDropzone({
  compact = false,
  disabled = false,
}: {
  compact?: boolean;
  /** Block drops while any document is actively ingesting. */
  disabled?: boolean;
}) {
  const upload = useUploadDocument();
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [failedFiles, setFailedFiles] = useState<string[]>([]);

  const handleFiles = (files: FileList | null) => {
    if (disabled || !files?.length) return;
    const list = Array.from(files);
    setFailedFiles([]);
    for (const file of list) {
      // `mutate` per file, not `mutateAsync`: each upload owns its own optimistic
      // row and its own error, so two rapid drops cannot clobber each other.
      upload.mutate(file, {
        onError: () => {
          setFailedFiles((current) =>
            current.includes(file.name) ? current : [...current, file.name],
          );
        },
      });
    }
  };

  const dismiss = () => {
    setFailedFiles([]);
    upload.reset();
  };

  // While any document is ingesting, show a non-interactive placeholder that
  // explains why the drop zone is locked. The backend serialises parsing behind
  // a single converter lock (no worker queue in V1), so a second file would
  // block indefinitely and the UI would appear stuck with no explanation.
  if (disabled) {
    return (
      <div
        aria-disabled="true"
        aria-label="Upload unavailable while a document is processing"
        className={[
          "rounded-lg border border-dashed border-border transition-colors",
          compact ? "px-3 py-2" : "px-4 py-8 text-center",
          "cursor-not-allowed opacity-50",
        ].join(" ")}
      >
        {compact ? (
          <span className="text-[13px] text-text-muted">
            Processing… upload will resume shortly
          </span>
        ) : (
          <>
            <p className="text-[13px] text-text-muted">
              Ingestion in progress
            </p>
            <p className="mt-1 text-[12px] leading-snug text-text-muted">
              Upload another file once the current one finishes.
            </p>
          </>
        )}
      </div>
    );
  }

  return (
    <div className="space-y-1.5">
      <div
        onDragOver={(event) => {
          event.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(event) => {
          event.preventDefault();
          setDragging(false);
          handleFiles(event.dataTransfer.files);
        }}
        onClick={() => inputRef.current?.click()}
        onKeyDown={(event) => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            inputRef.current?.click();
          }
        }}
        role="button"
        tabIndex={0}
        aria-label="Upload a policy document"
        className={[
          "cursor-pointer rounded-lg border border-dashed transition-colors",
          dragging
            ? "border-accent bg-highlight"
            : "border-border hover:border-text-muted",
          compact ? "px-3 py-2" : "px-4 py-8 text-center",
        ].join(" ")}
      >
        <input
          ref={inputRef}
          type="file"
          className="hidden"
          multiple
          accept={acceptedTypes()}
          onChange={(event) => {
            handleFiles(event.target.files);
            // Reset so re-picking the same file still fires a change event.
            event.target.value = "";
          }}
        />

        {compact ? (
          <span className="text-[13px] text-text-muted">+ Upload document</span>
        ) : (
          <>
            <p
              className={[
                "text-[13px]",
                dragging ? "text-accent" : "text-text-primary",
              ].join(" ")}
            >
              Drop a policy document here
            </p>
            <p className="mt-1 text-[13px] text-text-muted">or click to browse</p>
            <p className="mt-3 text-[11px] tracking-wide text-text-muted">
              PDF · DOCX · XLSX · CSV
            </p>
          </>
        )}
      </div>

      {/*
        Request-level failure, shown outside the drop target so it survives the
        target's hover states and cannot be clipped by them. Dismissible, because
        it is a report about a past action rather than a persistent condition.
      */}
      {failedFiles.length ? (
        <div
          role="alert"
          className="flex items-start gap-1.5 rounded-md border border-warning/40 bg-highlight px-2 py-1.5"
        >
          <AlertCircle
            size={13}
            strokeWidth={2}
            className="mt-0.5 shrink-0 text-warning"
            aria-hidden
          />
          <div className="min-w-0 flex-1">
            <p className="text-[12px] leading-snug text-warning">
              {failedFiles.length === 1
                ? `Could not upload ${failedFiles[0]}.`
                : `Could not upload ${failedFiles.length} files.`}
            </p>
            <p className="mt-0.5 text-[11px] leading-snug text-text-muted">
              {upload.error instanceof Error
                ? upload.error.message
                : "The upload request failed."}
            </p>
          </div>
          <button
            type="button"
            onClick={dismiss}
            aria-label="Dismiss upload error"
            className="shrink-0 rounded p-0.5 text-text-muted hover:text-text-primary"
          >
            <X size={12} strokeWidth={2} />
          </button>
        </div>
      ) : null}
    </div>
  );
}
