"use client";

import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import { ClipboardEvent, FormEvent, KeyboardEvent, useRef, useState } from "react";
import {
  FileUp,
  ImagePlus,
  Plus,
  Send,
  ToggleLeft,
  ToggleRight,
  X,
} from "lucide-react";
import { copy, type Locale } from "@/lib/i18n";
import type { Attachment } from "@/lib/types";
import { createAttachment } from "@/lib/chat-store";
import { cn } from "@/lib/utils";
import { AttachmentPreview } from "@/components/chat/AttachmentPreview";

type ChatComposerProps = {
  attachments: Attachment[];
  disabled?: boolean;
  locale: Locale;
  onAttachmentsChange: (files: Attachment[]) => void;
  onPlanModeChange: (planMode: boolean) => void;
  onSend: (message: string, files: Attachment[]) => void;
  onValueChange: (value: string) => void;
  planMode: boolean;
  value: string;
};

export function ChatComposer({
  attachments,
  disabled = false,
  locale,
  onAttachmentsChange,
  onPlanModeChange,
  onSend,
  onValueChange,
  planMode,
  value,
}: ChatComposerProps) {
  const [dragging, setDragging] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const imageInputRef = useRef<HTMLInputElement | null>(null);
  const t = copy[locale];

  const addFiles = (fileList: FileList | File[]) => {
    const nextFiles = Array.from(fileList).map(createAttachment);
    if (!nextFiles.length) return;
    onAttachmentsChange([...attachments, ...nextFiles]);
  };

  const submit = () => {
    const trimmed = value.trim();
    if (disabled || (!trimmed && attachments.length === 0)) return;

    onSend(trimmed, attachments);
    onValueChange("");
    onAttachmentsChange([]);

    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
    }
  };

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    submit();
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (
      event.key === "Enter" &&
      !event.shiftKey &&
      !event.nativeEvent.isComposing
    ) {
      event.preventDefault();
      submit();
    }
  };

  const handlePaste = (event: ClipboardEvent<HTMLTextAreaElement>) => {
    const filesFromClipboard = Array.from(event.clipboardData.items)
      .filter((item) => item.kind === "file")
      .map((item) => item.getAsFile())
      .filter((file): file is File => Boolean(file));

    if (!filesFromClipboard.length) return;
    event.preventDefault();
    addFiles(filesFromClipboard);
  };

  const resizeTextarea = () => {
    const textarea = textareaRef.current;
    if (!textarea) return;
    textarea.style.height = "auto";
    textarea.style.height = `${Math.min(textarea.scrollHeight, 180)}px`;
  };

  return (
    <div className="composer-safe-bottom shrink-0 bg-surface px-3 pt-2 sm:px-5">
      <form
        className={cn(
          "w-full rounded-[28px] border border-line bg-surface p-2 shadow-composer transition",
          dragging && "border-accent bg-panel",
        )}
        onDragLeave={(event) => {
          if (event.currentTarget.contains(event.relatedTarget as Node)) return;
          setDragging(false);
        }}
        onDragOver={(event) => {
          event.preventDefault();
          setDragging(true);
        }}
        onDrop={(event) => {
          event.preventDefault();
          setDragging(false);
          addFiles(event.dataTransfer.files);
        }}
        onSubmit={handleSubmit}
      >
        {attachments.length > 0 && (
          <div className="px-2 pb-2 pt-1">
            <AttachmentPreview
              attachments={attachments}
              onRemove={(id) =>
                onAttachmentsChange(attachments.filter((file) => file.id !== id))
              }
            />
          </div>
        )}
        <div className="flex items-end gap-2">
          <input
            className="hidden"
            accept=".csv,.tsv,.xlsx,.xls,.pdf,.doc,.docx,.ppt,.pptx,.txt,.md,image/*"
            multiple
            onChange={(event) => {
              if (event.target.files) addFiles(event.target.files);
              event.currentTarget.value = "";
            }}
            ref={fileInputRef}
            type="file"
          />
          <input
            accept="image/*"
            className="hidden"
            multiple
            onChange={(event) => {
              if (event.target.files) addFiles(event.target.files);
              event.currentTarget.value = "";
            }}
            ref={imageInputRef}
            type="file"
          />

          <DropdownMenu.Root>
            <DropdownMenu.Trigger asChild>
              <button
                aria-label={t.addContent}
                className="mb-1 flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-muted transition hover:bg-panel hover:text-ink"
                type="button"
              >
                <Plus className="h-5 w-5" />
              </button>
            </DropdownMenu.Trigger>
            <DropdownMenu.Portal>
              <DropdownMenu.Content
                align="start"
                className="z-50 min-w-48 rounded-xl border border-line bg-surface p-1 text-sm shadow-lg"
                side="top"
                sideOffset={10}
              >
                <DropdownMenu.Item
                  className="flex cursor-pointer items-center gap-2 rounded-lg px-3 py-2 outline-none hover:bg-panel"
                  onSelect={() => fileInputRef.current?.click()}
                >
                  <FileUp className="h-4 w-4" />
                  {t.uploadGuide}
                </DropdownMenu.Item>
                <DropdownMenu.Item
                  className="flex cursor-pointer items-center gap-2 rounded-lg px-3 py-2 outline-none hover:bg-panel"
                  onSelect={() => imageInputRef.current?.click()}
                >
                  <ImagePlus className="h-4 w-4" />
                  {t.uploadImage}
                </DropdownMenu.Item>
              </DropdownMenu.Content>
            </DropdownMenu.Portal>
          </DropdownMenu.Root>

          <button
            aria-pressed={planMode}
            className={cn(
              "mb-1 flex h-9 shrink-0 items-center gap-1.5 rounded-full border px-2.5 text-xs font-medium transition sm:px-3",
              planMode
                ? "border-accent bg-accent text-surface shadow-sm"
                : "border-line bg-panel text-muted hover:border-accent hover:text-ink",
            )}
            onClick={() => onPlanModeChange(!planMode)}
            type="button"
          >
            {planMode ? (
              <ToggleRight className="h-4 w-4 shrink-0" />
            ) : (
              <ToggleLeft className="h-4 w-4 shrink-0" />
            )}
            <span className="hidden sm:inline">
              {t.planMode}
            </span>
          </button>

          <textarea
            className="max-h-44 min-h-11 flex-1 resize-none overflow-y-auto bg-transparent px-1 py-3 text-[15px] leading-6 text-ink outline-none placeholder:text-muted"
            disabled={disabled}
            onChange={(event) => {
              onValueChange(event.target.value);
              resizeTextarea();
            }}
            onKeyDown={handleKeyDown}
            onPaste={handlePaste}
            placeholder={t.composerPlaceholder}
            ref={textareaRef}
            rows={1}
            value={value}
          />

          {value && (
            <button
              aria-label={t.clearInput}
              className="mb-1 hidden h-9 w-9 shrink-0 items-center justify-center rounded-full text-muted transition hover:bg-panel hover:text-ink sm:flex"
              onClick={() => onValueChange("")}
              type="button"
            >
              <X className="h-4 w-4" />
            </button>
          )}

          <button
            aria-label={t.send}
            className="mb-1 flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-ink text-surface transition hover:opacity-85 disabled:cursor-not-allowed disabled:bg-line disabled:text-muted"
            disabled={disabled || (!value.trim() && attachments.length === 0)}
            type="submit"
          >
            <Send className="h-4 w-4" />
          </button>
        </div>
      </form>
      <p className="mt-2 w-full text-center text-xs text-muted">
        {planMode
          ? `${t.planModeOn}${locale === "zh" ? "。" : ". "}${t.planModeHint}`
          : t.disclaimer}
      </p>
    </div>
  );
}
