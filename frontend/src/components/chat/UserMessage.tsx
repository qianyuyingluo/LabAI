"use client";

import type { Attachment, ChatMessage } from "@/lib/types";
import { copy, type Locale } from "@/lib/i18n";
import { AttachmentPreview } from "@/components/chat/AttachmentPreview";

type UserMessageProps = {
  message: ChatMessage;
  content: string;
  attachments?: Attachment[];
  locale: Locale;
};

export function UserMessage({
  attachments = [],
  content,
  locale,
  message,
}: UserMessageProps) {
  const t = copy[locale];

  return (
    <div className="flex justify-end">
      <div className="max-w-[92%] space-y-2 rounded-2xl bg-panel px-4 py-3 text-[15px] leading-7 text-ink shadow-sm sm:max-w-[86%]">
        {message.planMode && (
          <div className="inline-flex rounded-full border border-line bg-surface px-2 py-0.5 text-xs text-muted">
            {t.planMode}
          </div>
        )}
        {attachments.length > 0 && (
          <AttachmentPreview attachments={attachments} compact />
        )}
        {content && <div className="whitespace-pre-wrap break-words">{content}</div>}
      </div>
    </div>
  );
}
