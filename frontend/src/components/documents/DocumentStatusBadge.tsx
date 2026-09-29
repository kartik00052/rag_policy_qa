/**
 * Status badge (PROJECT.md Section 11.2).
 *
 * Ready and failed get a colour token; in-flight stages get the accent dot and
 * their real stage name, because the backend genuinely tracks them. Section
 * 11.2 is explicit that the text must not be a generic "Loading…".
 */

import { AlertCircle, Check } from "lucide-react";
import type { DocumentStatus } from "@/types/document";
import { stageLabel } from "@/types/document";

export function DocumentStatusBadge({
  status,
}: {
  status: DocumentStatus;
}) {
  if (status === "ready") {
    return (
      <span className="inline-flex items-center gap-1 text-[12px] text-success">
        <Check size={12} strokeWidth={2.25} aria-hidden />
        Ready
      </span>
    );
  }

  if (status === "failed") {
    return (
      <span className="inline-flex items-center gap-1 text-[12px] text-warning">
        <AlertCircle size={12} strokeWidth={2.25} aria-hidden />
        Failed
      </span>
    );
  }

  return (
    <span className="inline-flex items-center gap-1.5 text-[12px] text-text-muted">
      <span className="h-1.5 w-1.5 rounded-full bg-accent" aria-hidden />
      {stageLabel(status)}
    </span>
  );
}

