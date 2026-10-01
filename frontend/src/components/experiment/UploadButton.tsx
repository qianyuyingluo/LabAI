"use client";

import { ChangeEvent, useRef } from "react";
import { Upload } from "lucide-react";
import { createAttachment } from "@/lib/chat-store";
import { copy, type Locale } from "@/lib/i18n";
import type { Attachment } from "@/lib/types";

type UploadButtonProps = {
  locale: Locale;
  onFilesAdded: (files: Attachment[]) => void;
};

export function UploadButton({ locale, onFilesAdded }: UploadButtonProps) {
  const inputRef = useRef<HTMLInputElement | null>(null);
  const t = copy[locale];

  const handleChange = (event: ChangeEvent<HTMLInputElement>) => {
    if (event.target.files) {
      onFilesAdded(Array.from(event.target.files).map(createAttachment));
    }
    event.currentTarget.value = "";
  };

  return (
    <>
      <input
        className="hidden"
        multiple
        onChange={handleChange}
        ref={inputRef}
        type="file"
      />
      <button
        className="flex h-9 items-center gap-2 rounded-lg px-2.5 text-sm text-ink hover:bg-panel"
        onClick={() => inputRef.current?.click()}
        type="button"
      >
        <Upload className="h-4 w-4 text-muted" />
        <span className="hidden md:inline">{t.uploadGuide}</span>
      </button>
    </>
  );
}
