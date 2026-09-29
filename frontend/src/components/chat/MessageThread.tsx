/**
 * Chat thread (PROJECT.md Section 11.3).
 *
 * Owns nothing but layout: the messages come from `conversationStore` and the
 * empty-conversation state is the designed one from Section 11.3, not a
 * placeholder. Auto-scrolls only while the user is already at the bottom, so
 * scrolling up to re-read a citation does not yank them back down.
 */

import { useEffect, useRef } from "react";
import { useConversationStore } from "@/stores/conversationStore";
import { MessageBubble } from "./MessageBubble";
import { ChatInput } from "./ChatInput";

export function MessageThread() {
  const messages = useConversationStore((state) => state.messages);
  const isStreaming = useConversationStore((state) => state.isStreaming);
  const bottomRef = useRef<HTMLDivElement>(null);
  const pinnedToBottom = useRef(true);

  const handleScroll = () => {
    const element = bottomRef.current?.parentElement;
    if (!element) return;
    const distance =
      element.scrollHeight - element.scrollTop - element.clientHeight;
    // A small tolerance, so a sub-pixel rounding at the end of a long answer
    // does not count as "the user scrolled away".
    pinnedToBottom.current = distance < 40;
  };

  useEffect(() => {
    if (pinnedToBottom.current) {
      bottomRef.current?.scrollIntoView({ block: "end" });
    }
  }, [messages, isStreaming]);

  return (
    <section className="flex min-h-0 flex-1 flex-col bg-bg">
      <h2 className="shrink-0 px-6 pt-5 text-center font-display text-[13px] font-medium uppercase tracking-[0.14em] text-text-muted">
        Ask your policies
      </h2>

      <div
        className="min-h-0 flex-1 overflow-y-auto"
        onScroll={handleScroll}
      >
        <div className="mx-auto w-full max-w-[720px] px-6 py-5">
          {messages.length === 0 ? (
            <EmptyConversation />
          ) : (
            <div className="space-y-5">
              {messages.map((message) => (
                <MessageBubble key={message.id} message={message} />
              ))}
            </div>
          )}
          <div ref={bottomRef} />
        </div>
      </div>

      <div className="shrink-0 px-6 pb-4">
        <div className="mx-auto w-full max-w-[720px]">
          <ChatInput />
        </div>
      </div>
    </section>
  );
}

function EmptyConversation() {
  return (
    <div className="py-16 text-center">
      <p className="mx-auto max-w-[420px] text-[14px] leading-relaxed text-text-muted">
        Ask a question about any uploaded policy — answers will always cite the
        exact source.
      </p>
      <p className="mt-4 text-[13px] text-text-muted">
        Try:{' '}
        <span className="text-accent">
          &ldquo;What is the leave carry-forward limit?&rdquo;
        </span>
      </p>
    </div>
  );
}
