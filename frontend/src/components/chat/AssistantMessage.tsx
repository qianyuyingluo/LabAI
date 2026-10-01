"use client";

import * as Dialog from "@radix-ui/react-dialog";
import { useState } from "react";
import {
  Beaker,
  Check,
  ChevronDown,
  Copy,
  Download,
  File,
  FileSearch,
  ImageIcon,
  Loader2,
  Maximize2,
  RotateCcw,
  Terminal,
  TriangleAlert,
  X,
} from "lucide-react";
import { copy, type Locale } from "@/lib/i18n";
import type { ChatMessage, GeneratedFile } from "@/lib/types";
import { getUploadedFileUrl, getUploadedImageUrl } from "@/lib/api";
import { StreamingMarkdown } from "@/components/chat/StreamingMarkdown";

type AssistantMessageProps = {
  content: string;
  isStreaming: boolean;
  locale: Locale;
  message: ChatMessage;
};

export function AssistantMessage({
  content,
  isStreaming,
  locale,
  message,
}: AssistantMessageProps) {
  const t = copy[locale];
  const [filesOpen, setFilesOpen] = useState(false);
  const filesSkill = message.filesSkill;
  const filesSkillRunning = filesSkill?.status === "running";
  const filesSkillLabel =
    locale === "zh"
      ? filesSkillRunning
        ? "正在使用 files_skill"
        : "files_skill"
      : filesSkillRunning
        ? "Using files_skill"
        : "files_skill";

  return (
    <div className="group flex gap-3">
      <div className="mt-1 flex h-7 w-7 shrink-0 items-center justify-center rounded-full border border-line bg-surface text-accent">
        <Beaker className="h-4 w-4" />
      </div>
      <div className="min-w-0 flex-1">
        {filesSkill && (
          <div className="mb-2 rounded-lg border border-line bg-panel px-3 py-2 text-xs text-muted">
            <button
              className="flex w-full items-center justify-between gap-3 text-left"
              onClick={() => setFilesOpen((open) => !open)}
              type="button"
            >
              <span className="flex min-w-0 items-center gap-2">
                {filesSkillRunning ? (
                  <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-accent" />
                ) : (
                  <FileSearch className="h-3.5 w-3.5 shrink-0 text-accent" />
                )}
                <span className="truncate font-medium text-ink">{filesSkillLabel}</span>
                {filesSkill.files?.length ? (
                  <span className="shrink-0">{filesSkill.files.length} file(s)</span>
                ) : null}
                {filesSkill.truncated ? <span className="shrink-0">truncated</span> : null}
              </span>
              <ChevronDown
                className={`h-3.5 w-3.5 shrink-0 transition ${filesOpen ? "rotate-180" : ""}`}
              />
            </button>
            {filesOpen && (
              <div className="mt-2 space-y-2 border-t border-line pt-2">
                {filesSkill.files?.length ? (
                  <div className="space-y-1">
                    {filesSkill.files.map((file) => (
                      <div className="space-y-1 rounded-md border border-line bg-surface p-2" key={file.fileId}>
                        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                          <span className="font-medium text-ink">{file.filename}</span>
                          <span>{file.fileType}</span>
                          <span>{file.includedChars}/{file.extractedChars} chars</span>
                          {file.skillOutputChars ? <span>{file.skillOutputChars} answer chars</span> : null}
                          {file.truncated ? <span>truncated</span> : null}
                          {file.error ? <span className="text-red-600">{file.error}</span> : null}
                          {file.skillError ? <span className="text-red-600">{file.skillError}</span> : null}
                        </div>
                        {file.skillOutput ? (
                          <pre className="max-h-44 overflow-auto whitespace-pre-wrap text-[11px] leading-5 text-muted">
                            {file.skillOutput}
                          </pre>
                        ) : null}
                      </div>
                    ))}
                  </div>
                ) : null}
                {filesSkill.contextPreview ? (
                  <pre className="max-h-56 overflow-auto whitespace-pre-wrap rounded-md border border-line bg-surface p-2 text-[11px] leading-5 text-muted">
                    {filesSkill.contextPreview}
                  </pre>
                ) : null}
                {filesSkill.errors?.length ? (
                  <div className="space-y-1 text-red-600">
                    {filesSkill.errors.map((error) => (
                      <div key={error}>{error}</div>
                    ))}
                  </div>
                ) : null}
              </div>
            )}
          </div>
        )}
        <StreamingMarkdown
          content={content}
          emptyLabel={t.assistantEmpty}
          isStreaming={isStreaming}
          locale={locale}
        />
        <SandboxOutput locale={locale} message={message} />
        <div className="mt-2 flex items-center gap-1 text-muted opacity-80 transition-opacity group-hover:opacity-100">
          <button
            aria-label={t.copyReply}
            className="rounded-md p-1.5 hover:bg-panel hover:text-ink"
            type="button"
          >
            <Copy className="h-4 w-4" />
          </button>
          <button
            aria-label={t.retry}
            className="rounded-md p-1.5 hover:bg-panel hover:text-ink"
            type="button"
          >
            <RotateCcw className="h-4 w-4" />
          </button>
          {message.status === "done" && (
            <span className="ml-1 inline-flex items-center gap-1 text-xs">
              <Check className="h-3.5 w-3.5" />
              {t.done}
            </span>
          )}
          {isStreaming && (
            <span className="ml-1 text-xs text-muted">{t.analysisRunning}</span>
          )}
        </div>
      </div>
    </div>
  );
}

function SandboxOutput({ locale, message }: { locale: Locale; message: ChatMessage }) {
  const [previewFile, setPreviewFile] = useState<GeneratedFile | null>(null);
  const sandbox = message.pythonSandbox;
  const generatedFiles = message.generatedFiles ?? [];
  if (!sandbox && generatedFiles.length === 0) return null;

  const isRunning = sandbox?.status === "running";
  const isError = sandbox?.status === "error" || sandbox?.status === "failed";
  const sandboxLabel = getSandboxLabel(sandbox?.status, locale);
  const callsLabel = sandbox
    ? locale === "zh"
      ? `调用 ${sandbox.callCount} 次`
      : `${sandbox.callCount} call${sandbox.callCount === 1 ? "" : "s"}`
    : "";

  return (
    <div className="mt-3 space-y-2">
      {sandbox ? (
        <div
          className={`rounded-lg border px-3 py-2 text-xs ${
            isError
              ? "border-red-200 bg-red-50 text-red-700"
              : "border-line bg-panel text-muted"
          }`}
          data-testid="python-sandbox-status"
        >
          <div className="flex items-center gap-2">
            {isRunning ? (
              <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-accent" />
            ) : isError ? (
              <TriangleAlert className="h-3.5 w-3.5 shrink-0" />
            ) : (
              <Terminal className="h-3.5 w-3.5 shrink-0 text-accent" />
            )}
            <span className={isError ? "font-medium" : "font-medium text-ink"}>
              {sandboxLabel}
            </span>
            <span className="ml-auto shrink-0">{callsLabel}</span>
          </div>
          {sandbox.error ? (
            <p className="mt-1.5 whitespace-pre-wrap break-words leading-5">{sandbox.error}</p>
          ) : null}
        </div>
      ) : null}

      {generatedFiles.length ? (
        <section
          aria-label={locale === "zh" ? "生成的文件" : "Generated files"}
          className="rounded-lg border border-line bg-panel p-3"
          data-testid="generated-files"
        >
          <div className="mb-2 flex items-center gap-2 text-xs font-medium text-ink">
            <File className="h-3.5 w-3.5 text-accent" />
            <span>{locale === "zh" ? "生成的文件" : "Generated files"}</span>
            <span className="text-muted">{generatedFiles.length}</span>
          </div>
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
            {generatedFiles.map((file) => (
              <GeneratedFileCard
                file={file}
                key={file.fileId}
                locale={locale}
                onPreview={setPreviewFile}
              />
            ))}
          </div>
        </section>
      ) : null}
      <GeneratedImageDialog
        file={previewFile}
        locale={locale}
        onClose={() => setPreviewFile(null)}
      />
    </div>
  );
}

function GeneratedFileCard({
  file,
  locale,
  onPreview,
}: {
  file: GeneratedFile;
  locale: Locale;
  onPreview: (file: GeneratedFile) => void;
}) {
  if (file.status === "ready" && isGeneratedImage(file)) {
    return <GeneratedImageCard file={file} locale={locale} onPreview={onPreview} />;
  }

  const details = getFileDetails(file, locale);
  const content = (
    <>
      <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-surface text-accent">
        {file.status === "ready" ? (
          <Download className="h-4 w-4" />
        ) : (
          <TriangleAlert className="h-4 w-4 text-muted" />
        )}
      </span>
      <span className="min-w-0 flex-1">
        <span className="block truncate text-sm font-medium text-ink" title={file.name}>
          {file.name}
        </span>
        <span className="mt-0.5 block truncate text-[11px] text-muted">
          {file.status === "ready"
            ? details
            : locale === "zh"
              ? "文件不可用"
              : "File unavailable"}
        </span>
      </span>
    </>
  );

  return file.status === "ready" ? (
    <a
      className="flex min-w-0 items-center gap-2 rounded-lg border border-line bg-surface p-2 transition hover:border-accent hover:bg-white"
      download={file.name}
      href={getUploadedFileUrl(file.fileId)}
      rel="noreferrer"
      target="_blank"
      title={file.mimeType}
    >
      {content}
    </a>
  ) : (
    <div
      aria-disabled="true"
      className="flex min-w-0 items-center gap-2 rounded-lg border border-line bg-surface p-2 opacity-60"
      title={file.mimeType}
    >
      {content}
    </div>
  );
}

function GeneratedImageCard({
  file,
  locale,
  onPreview,
}: {
  file: GeneratedFile;
  locale: Locale;
  onPreview: (file: GeneratedFile) => void;
}) {
  const expandLabel = locale === "zh" ? "点击放大" : "Click to enlarge";
  const downloadLabel = locale === "zh" ? "下载" : "Download";

  return (
    <div
      className="min-w-0 overflow-hidden rounded-lg border border-line bg-surface"
      data-testid="generated-image-card"
    >
      <button
        aria-label={`${expandLabel}：${file.name}`}
        className="group/image relative block h-40 w-full overflow-hidden bg-panel p-2 text-left focus:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-inset"
        onClick={() => onPreview(file)}
        type="button"
      >
        {/* eslint-disable-next-line @next/next/no-img-element -- Generated images are served dynamically by the FastAPI backend. */}
        <img
          alt={file.name}
          className="h-full w-full object-contain transition duration-200 group-hover/image:scale-[1.02]"
          loading="lazy"
          src={getUploadedImageUrl(file.fileId)}
        />
        <span className="absolute right-2 top-2 inline-flex items-center gap-1 rounded-md bg-ink/75 px-2 py-1 text-[11px] font-medium text-white shadow-sm">
          <Maximize2 className="h-3 w-3" />
          {expandLabel}
        </span>
      </button>
      <div className="flex min-w-0 items-center gap-2 border-t border-line p-2">
        <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-panel text-accent">
          <ImageIcon className="h-4 w-4" />
        </span>
        <span className="min-w-0 flex-1">
          <span className="block truncate text-sm font-medium text-ink" title={file.name}>
            {file.name}
          </span>
          <span className="mt-0.5 block truncate text-[11px] text-muted">
            {getFileDetails(file, locale)}
          </span>
        </span>
        <a
          aria-label={`${downloadLabel} ${file.name}`}
          className="inline-flex shrink-0 items-center gap-1 rounded-md border border-line px-2 py-1.5 text-xs font-medium text-ink transition hover:border-accent hover:bg-panel"
          download={file.name}
          href={getUploadedFileUrl(file.fileId)}
          rel="noreferrer"
          target="_blank"
          title={`${downloadLabel} ${file.name}`}
        >
          <Download className="h-3.5 w-3.5" />
          {downloadLabel}
        </a>
      </div>
    </div>
  );
}

function GeneratedImageDialog({
  file,
  locale,
  onClose,
}: {
  file: GeneratedFile | null;
  locale: Locale;
  onClose: () => void;
}) {
  const downloadLabel = locale === "zh" ? "下载图片" : "Download image";

  return (
    <Dialog.Root
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      open={Boolean(file)}
    >
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-[70] bg-black/75 backdrop-blur-sm" />
        {file ? (
          <Dialog.Content
            className="fixed left-1/2 top-1/2 z-[71] flex max-h-[94vh] w-[min(1120px,calc(100vw-24px))] -translate-x-1/2 -translate-y-1/2 flex-col overflow-hidden rounded-xl border border-white/15 bg-slate-950 text-white shadow-2xl focus:outline-none"
            data-testid="generated-image-dialog"
          >
            <Dialog.Title className="sr-only">{file.name}</Dialog.Title>
            <Dialog.Description className="sr-only">
              {locale === "zh" ? "生成图片的大图预览" : "Large preview of the generated image"}
            </Dialog.Description>
            <div className="relative flex min-h-0 flex-1 items-center justify-center overflow-auto p-3 sm:p-5">
              {/* eslint-disable-next-line @next/next/no-img-element -- Generated images are served dynamically by the FastAPI backend. */}
              <img
                alt={file.name}
                className="max-h-[calc(94vh-88px)] max-w-full object-contain"
                src={getUploadedImageUrl(file.fileId)}
              />
              <Dialog.Close asChild>
                <button
                  aria-label={locale === "zh" ? "关闭图片预览" : "Close image preview"}
                  className="absolute right-3 top-3 rounded-full bg-black/65 p-2 text-white shadow transition hover:bg-black/85 focus:outline-none focus-visible:ring-2 focus-visible:ring-white"
                  type="button"
                >
                  <X className="h-5 w-5" />
                </button>
              </Dialog.Close>
            </div>
            <div className="flex shrink-0 items-center gap-3 border-t border-white/15 bg-slate-900 px-3 py-2.5 sm:px-4">
              <span className="min-w-0 flex-1 truncate text-sm font-medium" title={file.name}>
                {file.name}
              </span>
              <a
                className="inline-flex shrink-0 items-center gap-1.5 rounded-md bg-white px-3 py-2 text-xs font-semibold text-slate-950 transition hover:bg-slate-100"
                download={file.name}
                href={getUploadedFileUrl(file.fileId)}
                rel="noreferrer"
                target="_blank"
              >
                <Download className="h-4 w-4" />
                {downloadLabel}
              </a>
            </div>
          </Dialog.Content>
        ) : null}
      </Dialog.Portal>
    </Dialog.Root>
  );
}

function isGeneratedImage(file: GeneratedFile) {
  return file.fileType.toLowerCase() === "image" && file.mimeType.toLowerCase().startsWith("image/");
}

function getFileDetails(file: GeneratedFile, locale: Locale) {
  return [file.fileType, formatFileSize(file.size, locale)].filter(Boolean).join(" · ");
}

function getSandboxLabel(status: string | undefined, locale: Locale) {
  if (locale === "zh") {
    if (status === "running") return "Python 沙箱正在运行";
    if (status === "error" || status === "failed") return "Python 沙箱运行失败";
    if (status === "skipped") return "Python 沙箱未运行";
    if (status === "done" || status === "completed") return "Python 沙箱已完成";
    return status ? `Python 沙箱：${status}` : "Python 沙箱";
  }
  if (status === "running") return "Python sandbox is running";
  if (status === "error" || status === "failed") return "Python sandbox failed";
  if (status === "skipped") return "Python sandbox was not run";
  if (status === "done" || status === "completed") return "Python sandbox completed";
  return status ? `Python sandbox: ${status}` : "Python sandbox";
}

function formatFileSize(size: number, locale: Locale) {
  if (!Number.isFinite(size) || size <= 0) return "";
  if (size < 1024) return `${size} B`;
  const units = ["KB", "MB", "GB"];
  let value = size / 1024;
  let unitIndex = 0;
  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024;
    unitIndex += 1;
  }
  return `${value.toLocaleString(locale === "zh" ? "zh-CN" : "en-US", {
    maximumFractionDigits: value >= 10 ? 1 : 2,
  })} ${units[unitIndex]}`;
}
