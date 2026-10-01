"use client";

import { memo, RefObject, useCallback } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import type { Locale } from "@/lib/i18n";
import type { ChatMessage } from "@/lib/types";
import { estimateMessageHeight } from "@/lib/markdown";
import { MessageItem } from "@/components/chat/MessageItem";

type MessageListProps = {
  getMessage: (id: string) => ChatMessage | undefined;
  locale: Locale;
  messageIds: string[];
  onApprovePlan: (messageId: string, planId: string) => void;
  onRevisePlan: (messageId: string, planId: string, feedback: string) => void;
  scrollRef: RefObject<HTMLElement | null>;
  streamingContent: string;
  streamingMessageId?: string;
};

export const MessageList = memo(function MessageList({
  getMessage,
  locale,
  messageIds,
  onApprovePlan,
  onRevisePlan,
  scrollRef,
  streamingContent,
  streamingMessageId,
}: MessageListProps) {
  const useVirtualList = messageIds.length > 80;

  const estimateSize = useCallback(
    (index: number) => estimateMessageHeight(getMessage(messageIds[index])),
    [getMessage, messageIds],
  );

  const virtualizer = useVirtualizer({
    count: messageIds.length,
    enabled: useVirtualList,
    estimateSize,
    getItemKey: (index) => messageIds[index],
    getScrollElement: () => scrollRef.current,
    overscan: 8,
  });

  if (!useVirtualList) {
    return (
      <div className="space-y-5">
        {messageIds.map((id) => {
          const message = getMessage(id);
          if (!message) return null;
          const isStreaming = message.status === "streaming" || id === streamingMessageId;

          return (
            <MessageItem
              isStreaming={isStreaming}
              key={id}
              locale={locale}
              message={message}
              onApprovePlan={onApprovePlan}
              onRevisePlan={onRevisePlan}
              streamingContent={id === streamingMessageId ? streamingContent : undefined}
            />
          );
        })}
      </div>
    );
  }

  return (
    <div
      className="relative w-full"
      style={{ height: `${virtualizer.getTotalSize()}px` }}
    >
      {virtualizer.getVirtualItems().map((virtualRow) => {
        const id = messageIds[virtualRow.index];
        const message = getMessage(id);
        if (!message) return null;
        const isStreaming = message.status === "streaming" || id === streamingMessageId;

        return (
          <div
            data-index={virtualRow.index}
            key={id}
            ref={virtualizer.measureElement}
            style={{
              left: 0,
              position: "absolute",
              top: 0,
              transform: `translateY(${virtualRow.start}px)`,
              width: "100%",
            }}
          >
            <MessageItem
              isStreaming={isStreaming}
              locale={locale}
              message={message}
              onApprovePlan={onApprovePlan}
              onRevisePlan={onRevisePlan}
              streamingContent={id === streamingMessageId ? streamingContent : undefined}
            />
          </div>
        );
      })}
    </div>
  );
});
