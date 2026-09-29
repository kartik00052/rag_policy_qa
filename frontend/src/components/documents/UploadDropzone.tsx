/**
 * Drop target for uploads (PROJECT.md Section 11.5).
 *
 * Dashed `--border` outline, `--accent` on drag-over. On drop it hands the file
 * straight to `useUploadDocument` and disappears from view, because 11.5
 * specifies there is no separate modal or full-page uploading screen - the
 * sidebar shows the processing row instead.
 */

import { useRef, useState } from "react";
import { useUploadDocument } from "@/hooks/useUploadDocument";
import { acceptedTypes } from "@/types/document";

export function UploadDropzone({ compact = false }: { compact?: boolean }) {
  const upload = useUploadDocument();
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);

  const handleFiles = (files: FileList | null) => {
    if (!files?.length) return;
    // Sequential, not `forEach`: one upload at a time matches the sidebar's
    // single-row processing state, and keeps the poll loop unambiguous.
    void upload.mutate(files[0]);
  };

  return (
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

      {upload.isError ? (
        <p className="mt-2 text-[12px] text-warning">
          {upload.error.message}
        </p>
      ) : null}
    </div>
  );
}
