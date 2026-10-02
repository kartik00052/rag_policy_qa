import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useChatStream } from "../useChatStream";
import { useConversationStore } from "@/stores/conversationStore";
import * as api from "@/lib/api";
import { ApiError } from "@/lib/api";
import type { ChatFinal } from "@/types/chat";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    streamChat: vi.fn(),
    listConversations: vi.fn().mockResolvedValue([]),
  };
});

describe("useChatStream cancellation", () => {
  beforeEach(() => {
    useConversationStore.getState().reset();
    vi.clearAllMocks();
  });

  it("resets streaming state to false when request is cancelled", async () => {
    let cancelCallback: (() => void) | undefined;
    let rejectPromise: ((reason: unknown) => void) | undefined;

    const donePromise = new Promise<ChatFinal>((_, reject) => {
      rejectPromise = reject;
    });

    vi.mocked(api.streamChat).mockImplementation((_req, _callbacks) => {
      return {
        done: donePromise,
        cancel: () => {
          if (cancelCallback) cancelCallback();
          if (rejectPromise) {
            rejectPromise(new ApiError(0, "Request cancelled."));
          }
        },
      };
    });

    const { result } = renderHook(() => useChatStream());

    // Trigger send
    let sendPromise: Promise<void>;
    act(() => {
      sendPromise = result.current.send("Test question");
    });

    // Check that streaming started
    expect(useConversationStore.getState().isStreaming).toBe(true);
    expect(useConversationStore.getState().streamingMessageId).not.toBeNull();

    // Trigger cancellation by rejecting with "Request cancelled."
    await act(async () => {
      rejectPromise!(new ApiError(0, "Request cancelled."));
      try {
        await sendPromise;
      } catch {
        // Expected
      }
    });

    // ASSERT: After cancellation, isStreaming must return to false!
    expect(useConversationStore.getState().isStreaming).toBe(false);
    expect(useConversationStore.getState().streamingMessageId).toBeNull();

    // Assert assistant message is not left in an empty, permanently-streaming state
    const messages = useConversationStore.getState().messages;
    const assistantMsg = messages.find((m) => m.role === "assistant");
    expect(assistantMsg).toBeDefined();
    expect(assistantMsg?.isStreaming).toBe(false);
    expect(assistantMsg?.content.length).toBeGreaterThan(0);
  });
});
