/**
 * Chat streaming hook (PROJECT.md Section 11.3).
 *
 * Owns the request lifecycle and writes the results into `conversationStore`.
 * The store is the only place message state lives; this hook is the only place
 * that talks to `lib/api.ts`'s stream.
 *
 * Two details that come from how the backend actually behaves:
 *
 *  - A refusal sends no `token` events, only `done`. So the partial message
 *    stays empty until `done` fills it in. The UI must not treat "no tokens
 *    yet" as a failure - that would turn every correct refusal into an error.
 *  - The backend is validate-then-replay: `done.answer` is authoritative over
 *    the accumulated tokens, so `finalizeMessage` replaces the text rather than
 *    appending to it.
 */

import { useCallback, useRef } from "react";
import { ApiError, listConversations, streamChat } from "@/lib/api";
import { useConversationStore } from "@/stores/conversationStore";

let counter = 0;
function nextId(prefix: string): string {
  counter += 1;
  return `${prefix}-${Date.now()}-${counter}`;
}

export function useChatStream() {
  const conversationId = useConversationStore((state) => state.conversationId);
  const setConversationId = useConversationStore((state) => state.setConversationId);
  const addMessage = useConversationStore((state) => state.addMessage);
  const beginStreaming = useConversationStore((state) => state.beginStreaming);
  const appendToken = useConversationStore((state) => state.appendToken);
  const finalizeMessage = useConversationStore((state) => state.finalizeMessage);
  const failStream = useConversationStore((state) => state.failStream);

  // Held outside the store because it is a live transport handle, not UI state,
  // and nothing renders from it.
  const activeHandle = useRef<{ cancel: () => void } | null>(null);

  const send = useCallback(
    async (query: string) => {
      // A cancel rather than a silent no-op, so an impatient second Enter
      // produces a visible outcome instead of appearing to do nothing.
      activeHandle.current?.cancel();

      const now = new Date().toISOString();
      addMessage({
        id: nextId("u"),
        role: "user",
        content: query,
        createdAt: now,
        citations: [],
        hasSufficientEvidence: true,
      });

      const assistantId = nextId("a");
      addMessage({
        id: assistantId,
        role: "assistant",
        content: "",
        createdAt: new Date().toISOString(),
        citations: [],
        hasSufficientEvidence: true,
        isStreaming: true,
      });
      beginStreaming(assistantId);

      const handle = streamChat(
        { query, conversation_id: conversationId ?? undefined },
        {
          onToken: appendToken,
          onError: (error) => failStream(assistantId, error.detail),
        },
      );
      activeHandle.current = handle;
      try {
        const final = await handle.done;
        finalizeMessage(
          assistantId,
          final.answer,
          final.citations,
          final.has_sufficient_evidence,
        );
        try {
          const list = await listConversations();
          if (list.length > 0) {
            const latestId = list[0].id;
            setConversationId(latestId);
            localStorage.setItem("rag_active_conversation_id", latestId);
          }
        } catch {
          // Background sync
        }
      } catch (cause) {
        if (cause instanceof ApiError && cause.detail === "Request cancelled.") {
          return;
        }
        const detail =
          cause instanceof Error ? cause.message : "The request failed.";
        failStream(assistantId, detail);
      } finally {
        activeHandle.current = null;
      }
    },
    [
      addMessage,
      appendToken,
      beginStreaming,
      conversationId,
      failStream,
      finalizeMessage,
    ],
  );

  return { send };
}
