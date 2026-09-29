/**
 * Document contract, exactly as implemented (PROJECT.md Section 6,
 * `app/schemas/documents.py`).
 */

/**
 * Real ingestion stages. The sidebar cycles through these verbatim rather than
 * showing a generic "Loading..." (Section 11.2), because the backend genuinely
 * tracks them: `DocumentDetail.stage_index` / `stage_total` drive the position,
 * and `status` names the terminal ones.
 */
export const PIPELINE_STAGES = [
  "uploading",
  "parsing",
  "chunking",
  "embedding",
  "indexing",
  "ready",
] as const;

export type PipelineStage = (typeof PIPELINE_STAGES)[number];

export type DocumentStatus = PipelineStage | "failed";

export interface DocumentSummary {
  id: string;
  filename: string;
  file_type: string;
  status: DocumentStatus;
  created_at: string;
  chunk_count: number;
}

/** `GET /api/v1/documents/{id}` adds stage position; the list omits it. */
export interface DocumentDetail extends DocumentSummary {
  stage_index: number;
  stage_total: number;
}

/** `POST /api/v1/documents` */
export interface DocumentUploadResponse {
  document_id: string;
  status: DocumentStatus;
}

/** Human label for a stage, as shown in the sidebar's processing row. */
export function stageLabel(status: DocumentStatus): string {
  switch (status) {
    case "uploading":
      return "Uploading";
    case "parsing":
      return "Parsing";
    case "chunking":
      return "Chunking";
    case "embedding":
      return "Embedding";
    case "indexing":
      return "Indexing";
    case "ready":
      return "Ready";
    case "failed":
      return "Failed";
  }
}

/**
 * True once the document can no longer change state on its own, so polling
 * should stop. A `failed` document is terminal: `documents` has no
 * error-message column, so there is nothing further to poll for and nothing to
 * retry automatically.
 */
export function isTerminal(status: DocumentStatus): boolean {
  return status === "ready" || status === "failed";
}

/** Every non-terminal stage, i.e. the ones that still show a spinner. */
const PROCESSING: readonly PipelineStage[] = [
  "uploading",
  "parsing",
  "chunking",
  "embedding",
  "indexing",
];

/**
 * True while the document is still moving through the pipeline. Kept next to
 * `isTerminal` rather than in the badge component so the component file exports
 * only components.
 */
export function isProcessing(status: DocumentStatus): boolean {
  return PROCESSING.includes(status as PipelineStage);
}

/** Formats the backend accepts (Section 2 / 11.5). */
export const ACCEPTED_SUFFIXES = [".pdf", ".docx", ".xlsx", ".csv"] as const;

export function acceptedTypes(): string {
  return ACCEPTED_SUFFIXES.map((suffix) =>
    suffix === ".xlsx"
      ? "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
      : suffix === ".docx"
        ? "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        : suffix === ".csv"
          ? "text/csv"
          : "application/pdf",
  ).join(",");
}
