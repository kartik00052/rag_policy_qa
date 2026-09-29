/**
 * Mutable mock ingestion backend (Phase 2 / 11.2).
 *
 * The earlier fixtures were a static array, which quietly broke two things the
 * upload flow depends on:
 *
 *  1. A newly "uploaded" document was not in the list, so the very next poll
 *     deleted the optimistic row - it flickered into existence and vanished.
 *  2. Documents never advanced through the pipeline, so the polling hook was
 *     never actually exercised. Every processing state the sidebar renders came
 *     from hand-written fixture rows, not from the poll loop the real app runs.
 *
 * This module is a small in-memory backend instead: it holds real mutable state
 * and advances each in-flight document one stage per poll, on a wall clock, so
 * the sidebar's progression is produced by the same polling code path that runs
 * against the real backend.
 *
 * Stage timings are compressed (seconds, not the minutes a real CPU-only
 * embedding run takes) so the whole sequence is observable in a session, but the
 * ordering and the per-stage dwell time are genuine.
 */

import type { DocumentDetail, DocumentStatus, DocumentSummary } from "@/types/document";
import { MOCK_DOCUMENTS, MOCK_DOCUMENT_DETAIL } from "./fixtures";

/** How long a freshly uploaded document sits in each stage. */
const STAGE_DWELL_MS: Record<DocumentStatus, number> = {
  uploading: 600,
  parsing: 900,
  chunking: 800,
  embedding: 1400,
  indexing: 1000,
  ready: 0,
  failed: 0,
};

/** Order used to advance a document toward `ready`. */
const PROGRESSION: DocumentStatus[] = [
  "uploading",
  "parsing",
  "chunking",
  "embedding",
  "indexing",
  "ready",
];

interface MockInFlight {
  document: DocumentSummary;
  enteredStageAt: number;
}

let inFlight = new Map<string, MockInFlight>();
let uploadCounter = 0;

/** Documents seeded ready/failed from the fixtures. */
let seeded: DocumentSummary[] = [];
let initialized = false;

/**
 * Seeds the store once per page load.
 *
 * Seeded documents that start mid-pipeline are put into the same advancing map
 * as uploads, rather than left in a static list. Leaving them static was a real
 * defect: the list query polls while any document is non-terminal, so a fixture
 * parked permanently at `embedding` meant the sidebar polled every 1.2s forever
 * and never reached a steady state. Advancing them lets the mock converge to all
 * terminal, which is what the real system does, and lets polling stop.
 */
function ensureSeeded(): void {
  if (initialized) return;
  initialized = true;
  const now = Date.now();
  seeded = [];
  for (const document of MOCK_DOCUMENTS.map((d) => ({ ...d }))) {
    if (isTerminalStatus(document.status)) {
      seeded.push(document);
      continue;
    }
    inFlight.set(document.id, {
      document,
      // Stagger the seeded stages so they do not all flip in the same tick.
      enteredStageAt: now + (seeded.length % 3) * 250,
    });
  }
}

function isTerminalStatus(status: DocumentStatus): boolean {
  return status === "ready" || status === "failed";
}

export function resetMockDocuments(): void {
  inFlight = new Map();
  uploadCounter = 0;
  seeded = [];
  initialized = false;
  ensureSeeded();
}

function advance(document: MockInFlight, now: number): DocumentStatus {
  const dwell = STAGE_DWELL_MS[document.document.status];
  if (now - document.enteredStageAt < dwell) return document.document.status;

  const index = PROGRESSION.indexOf(document.document.status);
  if (index < 0 || index === PROGRESSION.length - 1) return document.document.status;

  const next = PROGRESSION[index + 1];
  document.document = {
    ...document.document,
    status: next,
    // A believable chunk count only appears once chunking is done, matching the
    // real pipeline rather than inventing chunks up front.
    chunk_count: next === "ready" ? 8 + (uploadCounter % 5) * 4 : document.document.chunk_count,
  };
  document.enteredStageAt = now;
  return next;
}

/** Registers an upload and returns the `POST /documents` response shape. */
export function mockUpload(filename: string, fileType: string): {
  document_id: string;
  status: DocumentStatus;
} {
  ensureSeeded();
  uploadCounter += 1;
  const id = `mock-up-${uploadCounter}-${Date.now().toString(36)}`;
  inFlight.set(id, {
    document: {
      id,
      filename,
      file_type: fileType,
      status: "uploading",
      created_at: new Date().toISOString(),
      chunk_count: 0,
    },
    enteredStageAt: Date.now(),
  });
  return { document_id: id, status: "uploading" };
}

/** Current list, with every in-flight document advanced to its due stage. */
export function mockList(): DocumentSummary[] {
  ensureSeeded();
  const now = Date.now();
  const live = [...inFlight.values()].map((entry) => ({
    ...advance(entry, now) && entry.document,
  }));
  return [...seeded, ...live].map((document) => ({ ...document }));
}

export function mockGet(documentId: string): DocumentDetail | null {
  ensureSeeded();
  const entry = inFlight.get(documentId);
  const summary =
    entry?.document ?? seeded.find((document) => document.id === documentId);
  if (!summary) return null;
  const detail: DocumentDetail = {
    ...summary,
    stage_index: Math.min(PROGRESSION.indexOf(summary.status) + 1, PROGRESSION.length),
    stage_total: PROGRESSION.length,
  };
  return detail;
}

/** Fallback detail for a fixture id with no dedicated entry. */
export function mockDefaultDetail(): DocumentDetail {
  return { ...MOCK_DOCUMENT_DETAIL };
}
