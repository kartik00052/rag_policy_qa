/**
 * Chat input (PROJECT.md Section 11.3).
 *
 * The uncommitted text is the one piece of state the conventions allow
 * locally (workflow.md Section 5): it is a draft belonging to this input and
 * meaningless anywhere else. Everything else - streaming, errors, the thread -
 * lives in the store.
 *
 * Enter sends, Shift+Enter newlines. Disabled while streaming so a second
 * question cannot interleave with the first one's tokens.
 */

import { useState } from "react";
import { ArrowUp } from "lucide-react";
import { useConversationStore } from "@/stores/conversationStore";
import { useChatStream } from "@/hooks/useChatStream";

export function ChatInput() {
  const [draft, setDraft] = useState("");
  const isStreaming = useConversationStore((state) => state.isStreaming);
  const { send } = useChatStream();

  const canSend = draft.trim().length > 0 && !isStreaming;

  const submit = () => {
    if (!canSend) return;
    const query = draft.trim();
    setDraft("");
    void send(query);
  };

  return (
    <div className="rounded-lg border border-border bg-surface focus-within:border-accent">
      <textarea
        value={draft}
        onChange={(event) => setDraft(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter" && !event.shiftKey) {
            event.preventDefault();
            submit();
          }
        }}
        rows={1}
        placeholder={
          isStreaming ? "Answering…" : "Ask a question about your policies…"
        }
        disabled={isStreaming}
        aria-label="Ask a question"
        className="max-h-40 w-full resize-none bg-transparent px-3 py-2 text-[14px] text-text-primary outline-none placeholder:text-text-muted disabled:opacity-60"
      />
      <div className="flex items-center justify-end px-2 pb-2">
        <button
          type="button"
          onClick={submit}
          disabled={!canSend}
          aria-label="Send question"
          className="flex h-7 w-7 items-center justify-center rounded-md bg-accent text-bg transition-colors hover:bg-accent-hover disabled:cursor-not-allowed disabled:bg-border disabled:text-text-muted"
        >
          <ArrowUp size={14} strokeWidth={2.25} />
        </button>
      </div>
    </div>
  );
}
