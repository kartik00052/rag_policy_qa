/**
 * One turn in the thread (PROJECT.md Section 11.3).
 *
 * AI answers render as plain text on `bg-bg` with no bubble container; user
 * messages are right-aligned. The citation chips are appended after the text
 * rather than spliced inline at character offsets, because the backend strips
 * its own `[n]` markers from the answer text before it is sent - what remains is
 * prose, and the citation list is attached to it.
 *
 * The streaming caret is rendered only while this message is actually receiving
 * tokens, and is the single animated element in the thread.
 */

import { motion } from "framer-motion";
import { AlertCircle } from "lucide-react";
import type { Message } from "@/types/chat";
import { CitationChip } from "./CitationChip";

function StreamingCaret() {
  return (
    <motion.span
      // Section 11.3 `▌`, animated as an opacity pulse while streaming.
      className="ml-0.5 inline-block h-[1em] w-[3px] translate-y-[2px] bg-accent align-baseline"
      animate={{ opacity: [1, 0.15, 1] }}
      transition={{ duration: 1, repeat: Infinity, ease: "easeInOut" }}
      aria-hidden
    />
  );
}

export function MessageBubble({ message }: { message: Message }) {
  if (message.role === "user") {
    return (
      <div className="flex justify-end">
        <div className="max-w-[85%] rounded-lg bg-surface px-3 py-2 text-[14px] text-text-primary">
          {message.content}
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {/*
        Section 11.3 no-evidence response: still a normal AI message, styled
        with `--warning`. Deliberately not an error treatment - the backend
        declined to answer from the retrieved evidence, which is a correct
        outcome rather than a failure.
      */}
      <div
        className={
          message.hasSufficientEvidence
            ? "text-[14px] leading-relaxed text-text-primary"
            : "text-[14px] leading-relaxed text-warning"
        }
      >
        {message.content}
        {message.isStreaming ? <StreamingCaret /> : null}
      </div>

      {message.error ? (
        <div className="flex items-start gap-1.5 rounded-md border border-warning/40 bg-highlight px-2 py-1.5">
          <AlertCircle
            size={13}
            strokeWidth={2}
            className="mt-0.5 shrink-0 text-warning"
            aria-hidden
          />
          <p className="text-[12px] leading-snug text-warning">{message.error}</p>
        </div>
      ) : null}

      {message.citations.length ? (
        <div className="flex flex-wrap gap-1.5 pt-0.5">
          {message.citations.map((citation) => (
            <CitationChip
              key={citation.id}
              citation={citation}
              siblings={message.citations}
            />
          ))}
        </div>
      ) : null}
    </div>
  );
}
