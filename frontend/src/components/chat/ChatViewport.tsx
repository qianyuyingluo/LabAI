"use client";

import { RefObject, useCallback, useEffect, useLayoutEffect, useRef } from "react";
import { ArrowDown } from "lucide-react";
import { copy, type Locale } from "@/lib/i18n";
import type { ChatMessage } from "@/lib/types";
import { useStickToBottom } from "@/hooks/use-stick-to-bottom";
import { MessageList } from "@/components/chat/MessageList";

type ChatViewportProps = {
  getMessage: (id: string) => ChatMessage | undefined;
  conversationKey: string;
  locale: Locale;
  messageIds: string[];
  onApprovePlan: (messageId: string, planId: string) => void;
  onPromptClick: (prompt: string) => void;
  onRevisePlan: (messageId: string, planId: string, feedback: string) => void;
  streamingContent: string;
  streamingMessageId?: string;
};

export function ChatViewport({
  getMessage,
  conversationKey,
  locale,
  messageIds,
  onApprovePlan,
  onPromptClick,
  onRevisePlan,
  streamingContent,
  streamingMessageId,
}: ChatViewportProps) {
  const t = copy[locale];
  const prompts = [
    t.promptGuide,
    t.promptFormula,
    t.promptCode,
    t.promptOutlier,
    t.promptReport,
  ];
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const bottomRef = useRef<HTMLDivElement | null>(null);
  const { isAtBottom, scrollToBottom } = useStickToBottom(
    scrollRef as RefObject<HTMLElement | null>,
    bottomRef as RefObject<HTMLElement | null>,
  );
  const openedConversationRef = useRef("");
  const forcedForConversationRef = useRef(false);
  const messageCountRef = useRef(messageIds.length);
  const restoreTimersRef = useRef<number[]>([]);

  const forceScrollToBottom = useCallback(() => {
    if (typeof window === "undefined") return;
    for (const timer of restoreTimersRef.current) {
      window.clearTimeout(timer);
    }
    restoreTimersRef.current = [];

    scrollToBottom(true);
    restoreTimersRef.current = [80, 240].map((delay) =>
      window.setTimeout(() => scrollToBottom(true), delay),
    );
  }, [scrollToBottom]);

  useEffect(() => {
    messageCountRef.current = messageIds.length;
  }, [messageIds.length]);

  useEffect(() => {
    scrollToBottom();
  }, [messageIds.length, scrollToBottom, streamingContent, streamingMessageId]);

  useLayoutEffect(() => {
    if (openedConversationRef.current !== conversationKey) {
      openedConversationRef.current = conversationKey;
      forcedForConversationRef.current = false;
    }

    if (!messageIds.length || forcedForConversationRef.current) return;
    forcedForConversationRef.current = true;
    forceScrollToBottom();
  }, [conversationKey, forceScrollToBottom, messageIds.length]);

  useEffect(() => {
    const restoreVisibleChatBottom = () => {
      if (!messageCountRef.current) return;
      forceScrollToBottom();
    };
    const handleVisibilityChange = () => {
      if (document.visibilityState === "visible") {
        restoreVisibleChatBottom();
      }
    };

    document.addEventListener("visibilitychange", handleVisibilityChange);
    window.addEventListener("pageshow", restoreVisibleChatBottom);

    return () => {
      document.removeEventListener("visibilitychange", handleVisibilityChange);
      window.removeEventListener("pageshow", restoreVisibleChatBottom);
      for (const timer of restoreTimersRef.current) {
        window.clearTimeout(timer);
      }
      restoreTimersRef.current = [];
    };
  }, [forceScrollToBottom]);

  const empty = messageIds.length === 0;

  return (
    <section className="relative min-h-0 flex-1 overflow-hidden bg-surface">
      <div
        className="app-scrollbar h-full overflow-y-auto px-3 pb-4 pt-5 sm:px-5"
        ref={scrollRef}
      >
        <div className="min-h-full w-full pb-8">
          {empty ? (
            <div className="flex min-h-[62vh] flex-col items-center justify-center px-3 text-center">
              <div className="mb-5 flex h-12 w-12 items-center justify-center rounded-full border border-line bg-panel text-xl font-semibold text-accent">
                AI
              </div>
              <h1 className="text-2xl font-semibold tracking-normal text-ink sm:text-3xl">
                {t.appName}
              </h1>
              <p className="mt-3 max-w-xl text-sm leading-6 text-muted">
                {t.heroSubtitle}
              </p>
              <div className="mt-8 grid w-full max-w-2xl grid-cols-1 gap-2 sm:grid-cols-2">
                {prompts.map((prompt) => (
                  <button
                    className="rounded-xl border border-line bg-surface px-4 py-3 text-left text-sm text-ink transition hover:bg-panel"
                    key={prompt}
                    onClick={() => onPromptClick(prompt)}
                    type="button"
                  >
                    {prompt}
                  </button>
                ))}
              </div>
            </div>
          ) : (
            <MessageList
              getMessage={getMessage}
              locale={locale}
              messageIds={messageIds}
              onApprovePlan={onApprovePlan}
              onRevisePlan={onRevisePlan}
              scrollRef={scrollRef}
              streamingContent={streamingContent}
              streamingMessageId={streamingMessageId}
            />
          )}
          <div className="h-1 w-full" ref={bottomRef} />
        </div>
      </div>

      {!isAtBottom && (
        <button
          aria-label={t.scrollBottom}
          className="absolute bottom-4 left-1/2 flex h-9 w-9 -translate-x-1/2 items-center justify-center rounded-full border border-line bg-surface text-ink shadow-md transition hover:bg-panel"
          onClick={() => scrollToBottom(true)}
          type="button"
        >
          <ArrowDown className="h-4 w-4" />
        </button>
      )}
    </section>
  );
}
