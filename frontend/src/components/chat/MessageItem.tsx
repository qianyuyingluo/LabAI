"use client";

import { memo, useRef } from "react";
import type { Locale } from "@/lib/i18n";
import type { ChatMessage } from "@/lib/types";
import { cn } from "@/lib/utils";
import { AssistantMessage } from "@/components/chat/AssistantMessage";
import { PlanCard } from "@/components/chat/PlanCard";
import { UserMessage } from "@/components/chat/UserMessage";

type MessageItemProps = {
  message: ChatMessage;
  isStreaming: boolean;
  locale: Locale;
  onApprovePlan: (messageId: string, planId: string) => void;
  onRevisePlan: (messageId: string, planId: string, feedback: string) => void;
  streamingContent?: string;
};

export const MessageItem = memo(
  function MessageItem({
    isStreaming,
    locale,
    message,
    onApprovePlan,
    onRevisePlan,
    streamingContent,
  }: MessageItemProps) {
    const renderCount = useRef(0);
    renderCount.current += 1;
    const content = isStreaming && streamingContent !== undefined ? streamingContent : message.content;

    return (
      <article
        className={cn(
          "message-shell py-2",
          isStreaming && "is-streaming",
          message.role === "user" ? "pl-10" : "pr-5",
        )}
        data-message-id={message.id}
        data-render-count={renderCount.current}
      >
        {message.role === "user" ? (
          <UserMessage
            attachments={message.attachments}
            content={content}
            locale={locale}
            message={message}
          />
        ) : message.type === "plan" ? (
          <PlanCard
            content={content}
            isStreaming={isStreaming}
            locale={locale}
            message={message}
            onApprovePlan={onApprovePlan}
            onRevisePlan={onRevisePlan}
          />
        ) : (
          <AssistantMessage
            content={content}
            isStreaming={isStreaming}
            locale={locale}
            message={message}
          />
        )}
      </article>
    );
  },
  (prev, next) =>
    prev.message === next.message &&
    prev.isStreaming === next.isStreaming &&
    prev.locale === next.locale &&
    prev.onApprovePlan === next.onApprovePlan &&
    prev.onRevisePlan === next.onRevisePlan &&
    prev.streamingContent === next.streamingContent,
);
