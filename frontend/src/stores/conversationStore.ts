/**
 * Conversation store (PROJECT.md Section 8).
 *
 * Owns the chat thread, including the in-flight streaming partial. Streaming
 * tokens arrive one `token` event at a time and must be appended in order, so
 * `appendToken` writes into one identified partial message rather than letting
 * each event create its own - otherwise the thread would fill with fragments.
 *
 * The store holds UI state only. Sending a request, parsing SSE and persistence
 * belong to `lib/api.ts`, `lib/sse.ts` and `hooks/useChatStream.ts`; this store
 * is what they write into.
 */

import { create } from "zustand";
import type { Citation } from "@/types/citation";
import type { Message, StoredMessage } from "@/types/chat";

interface ConversationStore {
  conversationId: string | null;
  messages: Message[];
  isStreaming: boolean;
  /** Id of the message currently receiving tokens, for the streaming caret. */
  streamingMessageId: string | null;
  /** Transport/provider failure, shown as a distinct state from a refusal. */
  error: string | null;

  appendToken: (token: string) => void;
  setCitations: (messageId: string, citations: Citation[]) => void;
  /** Set when the backend has no conversation to resume. */
  startConversation: () => void;
  setConversationId: (id: string) => void;
  addMessage: (message: Message) => void;
  /** Replace the whole thread, e.g. restoring history on reload. */
  setMessages: (messages: Message[]) => void;
  beginStreaming: (messageId: string) => void;
  /** Settle the partial into a final message from the `done` event. */
  finalizeMessage: (
    messageId: string,
    content: string,
    citations: Citation[],
    hasSufficientEvidence: boolean,
  ) => void;
  failStream: (messageId: string, error: string) => void;
  cancelStream: (messageId?: string) => void;
  clearError: () => void;
  reset: () => void;
}

/** Normalises a persisted row into the thread's shape. */
export function fromStoredMessage(stored: StoredMessage): Message {
  return {
    id: stored.id,
    role: stored.role,
    content: stored.content,
    createdAt: stored.created_at,
    citations: stored.citations ?? [],
    // Persisted rows predate nothing here, but the backend only records the flag
    // on generated answers. A missing flag means "not a refusal", since a real
    // refusal is always persisted with it.
    hasSufficientEvidence: stored.has_sufficient_evidence ?? true,
  };
}

export const useConversationStore = create<ConversationStore>((set, get) => ({
  conversationId: null,
  messages: [],
  isStreaming: false,
  streamingMessageId: null,
  error: null,

  appendToken: (token) => {
    if (!token) return;
    const { streamingMessageId } = get();
    if (!streamingMessageId) return;
    set((state) => ({
      messages: state.messages.map((message) =>
        message.id === streamingMessageId
          ? { ...message, content: message.content + token }
          : message,
      ),
    }));
  },

  setCitations: (messageId, citations) =>
    set((state) => ({
      messages: state.messages.map((message) =>
        message.id === messageId ? { ...message, citations } : message,
      ),
    })),

  startConversation: () =>
    set({ conversationId: null, messages: [], error: null }),

  setConversationId: (id) => set({ conversationId: id }),

  addMessage: (message) => set((state) => ({ messages: [...state.messages, message] })),

  setMessages: (messages) => set({ messages }),

  beginStreaming: (messageId) =>
    set({ isStreaming: true, streamingMessageId: messageId, error: null }),

  finalizeMessage: (messageId, content, citations, hasSufficientEvidence) =>
    set((state) => ({
      messages: state.messages.map((message) =>
        message.id === messageId
          ? {
              ...message,
              // The `done` payload is authoritative. Trusting it over the
              // accumulated tokens matters because the backend deliberately
              // replays the accepted text after validating it.
              content,
              citations,
              hasSufficientEvidence,
              isStreaming: false,
            }
          : message,
      ),
      isStreaming: false,
      streamingMessageId: null,
    })),

  failStream: (messageId, error) =>
    set((state) => ({
      messages: state.messages.map((message) =>
        message.id === messageId
          ? { ...message, isStreaming: false, error }
          : message,
      ),
      isStreaming: false,
      streamingMessageId: null,
      // Surfaced separately so a transport failure is visible even if the user
      // never looks at the message itself.
      error,
    })),

  cancelStream: (messageId) =>
    set((state) => {
      const targetId = messageId ?? state.streamingMessageId;
      return {
        messages: state.messages.map((message) => {
          if (message.id === targetId || message.isStreaming) {
            const hasContent =
              typeof message.content === "string" &&
              message.content.trim().length > 0;
            return {
              ...message,
              content: hasContent ? message.content : "Generation cancelled.",
              isStreaming: false,
            };
          }
          return message;
        }),
        isStreaming: false,
        streamingMessageId: null,
      };
    }),

  clearError: () => set({ error: null }),

  reset: () =>
    set({
      conversationId: null,
      messages: [],
      isStreaming: false,
      streamingMessageId: null,
      error: null,
    }),
}));

if (typeof window !== "undefined") {
  (window as unknown as { __conversationStore: typeof useConversationStore }).__conversationStore =
    useConversationStore;
}
