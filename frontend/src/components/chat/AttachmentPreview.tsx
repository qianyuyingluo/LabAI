"use client";

import { useEffect, useMemo } from "react";
import { FileText, ImageIcon, X } from "lucide-react";
import type { Attachment } from "@/lib/types";
import { formatBytes } from "@/lib/utils";

type AttachmentPreviewProps = {
  attachments: Attachment[];
  onRemove?: (id: string) => void;
  compact?: boolean;
};

export function AttachmentPreview({
  attachments,
  compact = false,
  onRemove,
}: AttachmentPreviewProps) {
  if (!attachments.length) return null;

  return (
    <div className="flex flex-wrap gap-2">
      {attachments.map((file) => (
        <AttachmentTile
          compact={compact}
          file={file}
          key={file.id}
          onRemove={onRemove}
        />
      ))}
    </div>
  );
}

type AttachmentTileProps = {
  compact: boolean;
  file: Attachment;
  onRemove?: (id: string) => void;
};

function AttachmentTile({ compact, file, onRemove }: AttachmentTileProps) {
  const objectUrl = useMemo(() => {
    if (!file.rawFile || !file.type.startsWith("image/")) return "";
    return URL.createObjectURL(file.rawFile);
  }, [file.rawFile, file.type]);
  const imageUrl = file.url || file.previewUrl || objectUrl;
  const isImage = file.type.startsWith("image/") && Boolean(imageUrl);

  useEffect(
    () => () => {
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    },
    [objectUrl],
  );

  if (isImage) {
    return (
      <div
        className="group relative overflow-hidden rounded-lg border border-line bg-panel shadow-sm"
        title={file.name}
      >
        <div className={compact ? "h-24 w-32" : "h-28 w-40"}>
          {/* eslint-disable-next-line @next/next/no-img-element -- Local uploaded images are served by the FastAPI backend. */}
          <img
            alt={file.name}
            className="h-full w-full object-cover"
            loading="lazy"
            src={imageUrl}
          />
        </div>
        <div className="absolute inset-x-0 bottom-0 bg-ink/70 px-2 py-1 text-[11px] text-surface">
          <div className="truncate font-medium">{file.name}</div>
          {!compact && <div className="opacity-80">{formatBytes(file.size)}</div>}
        </div>
        <div className="absolute left-1.5 top-1.5 rounded-md bg-surface/90 p-1 text-muted shadow-sm">
          <ImageIcon className="h-3.5 w-3.5" />
        </div>
        {onRemove && (
          <button
            aria-label={`移除 ${file.name}`}
            className="absolute right-1.5 top-1.5 rounded-md bg-surface/90 p-1 text-muted shadow-sm hover:text-ink"
            onClick={() => onRemove(file.id)}
            type="button"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        )}
      </div>
    );
  }

  return (
    <div
      className="flex max-w-64 items-center gap-2 rounded-lg border border-line bg-panel px-2.5 py-2 text-xs text-ink"
      title={file.name}
    >
      <FileText className="h-4 w-4 shrink-0 text-muted" />
      <div className="min-w-0">
        <div className="truncate font-medium">{file.name}</div>
        {!compact && <div className="text-muted">{formatBytes(file.size)}</div>}
      </div>
      {onRemove && (
        <button
          aria-label={`移除 ${file.name}`}
          className="rounded-md p-1 text-muted hover:bg-surface hover:text-ink"
          onClick={() => onRemove(file.id)}
          type="button"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      )}
    </div>
  );
}
