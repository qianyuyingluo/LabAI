"use client";

import * as Dialog from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import type { PlanPrompt } from "@/lib/types";

type PlanPromptDialogProps = {
  error?: string;
  loading?: boolean;
  onOpenChange: (open: boolean) => void;
  open: boolean;
  prompts: PlanPrompt[];
};

export function PlanPromptDialog({
  error = "",
  loading = false,
  onOpenChange,
  open,
  prompts,
}: PlanPromptDialogProps) {
  return (
    <Dialog.Root onOpenChange={onOpenChange} open={open}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-black/25" />
        <Dialog.Content className="fixed left-1/2 top-1/2 z-50 flex max-h-[82vh] w-[min(760px,calc(100vw-24px))] -translate-x-1/2 -translate-y-1/2 flex-col overflow-hidden rounded-lg border border-line bg-surface shadow-xl">
          <div className="flex h-12 shrink-0 items-center justify-between border-b border-line px-4">
            <Dialog.Title className="text-sm font-semibold text-ink">
              Plan Mode prompts
            </Dialog.Title>
            <Dialog.Close asChild>
              <button
                aria-label="Close Plan Mode prompts"
                className="rounded-lg p-2 text-muted hover:bg-panel hover:text-ink"
                type="button"
              >
                <X className="h-4 w-4" />
              </button>
            </Dialog.Close>
          </div>

          <div className="app-scrollbar min-h-0 flex-1 overflow-y-auto p-4">
            {loading && <div className="text-sm text-muted">Loading prompts...</div>}
            {error && !loading && (
              <div className="rounded-lg border border-line bg-panel px-3 py-2 text-sm text-ink">
                {error}
              </div>
            )}
            {!loading && !error && (
              <div className="space-y-3">
                {prompts.map((prompt) => (
                  <section
                    className="overflow-hidden rounded-lg border border-line"
                    key={prompt.key}
                  >
                    <div className="border-b border-line bg-panel px-3 py-2">
                      <div className="text-sm font-medium text-ink">{prompt.relative_path}</div>
                      <div className="mt-1 truncate font-mono text-xs text-muted">
                        {prompt.content_hash}
                      </div>
                    </div>
                    <pre className="max-h-72 overflow-auto whitespace-pre-wrap break-words bg-surface p-3 text-sm leading-6 text-ink">
                      {prompt.content || "(empty)"}
                    </pre>
                  </section>
                ))}
              </div>
            )}
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
