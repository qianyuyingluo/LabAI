"use client";

import * as Accordion from "@radix-ui/react-accordion";
import * as Dialog from "@radix-ui/react-dialog";
import { ChevronDown, Settings, X } from "lucide-react";
import { copy, type Locale } from "@/lib/i18n";
import { AdvancedSettings } from "@/components/settings/AdvancedSettings";
import { ApiSettingsForm } from "@/components/settings/ApiSettingsForm";
import {
  ExperimentProcessingSettings,
  GenerationSettings,
} from "@/components/settings/GenerationSettings";
import type { ModelProfile, ModelProfileInput } from "@/lib/types";

type SettingsSheetProps = {
  locale: Locale;
  models: ModelProfile[];
  selectedModelId: string;
  onCreateModel: (input: ModelProfileInput) => Promise<ModelProfile>;
  onDeleteModel: (id: string) => Promise<void>;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSelectModel: (id: string) => void;
  onTestModel: (id: string) => Promise<string>;
  onUpdateModel: (
    id: string,
    input: Partial<ModelProfileInput>,
  ) => Promise<ModelProfile>;
};

export function SettingsSheet({
  locale,
  models,
  onCreateModel,
  onDeleteModel,
  onOpenChange,
  onSelectModel,
  onTestModel,
  onUpdateModel,
  open,
  selectedModelId,
}: SettingsSheetProps) {
  const t = copy[locale];

  return (
    <Dialog.Root onOpenChange={onOpenChange} open={open}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-black/20" />
        <Dialog.Content className="fixed inset-y-0 right-0 z-50 flex w-full max-w-md flex-col border-l border-line bg-surface shadow-xl">
          <div className="flex h-14 shrink-0 items-center justify-between border-b border-line px-4">
            <Dialog.Title className="flex items-center gap-2 text-base font-semibold">
              <Settings className="h-4 w-4" />
              {t.settingsTitle}
            </Dialog.Title>
            <Dialog.Close asChild>
              <button
                aria-label={t.closeSettings}
                className="rounded-lg p-2 text-muted hover:bg-panel hover:text-ink"
                type="button"
              >
                <X className="h-4 w-4" />
              </button>
            </Dialog.Close>
          </div>

          <div className="app-scrollbar min-h-0 flex-1 overflow-y-auto p-4">
            <section className="space-y-3">
              <h2 className="text-sm font-semibold">{t.baseSettings}</h2>
              <p className="rounded-lg border border-line bg-panel px-3 py-2 text-xs leading-5 text-muted">
                {t.apiNote}
              </p>
              <ApiSettingsForm
                locale={locale}
                models={models}
                onCreateModel={onCreateModel}
                onDeleteModel={onDeleteModel}
                onSelectModel={onSelectModel}
                onTestModel={onTestModel}
                onUpdateModel={onUpdateModel}
                selectedModelId={selectedModelId}
              />
            </section>

            <section className="mt-6 space-y-3">
              <h2 className="text-sm font-semibold">{t.generationSettings}</h2>
              <GenerationSettings locale={locale} />
            </section>

            <section className="mt-6 space-y-3">
              <h2 className="text-sm font-semibold">{t.processingSettings}</h2>
              <ExperimentProcessingSettings locale={locale} />
            </section>

            <Accordion.Root className="mt-6" collapsible type="single">
              <Accordion.Item className="rounded-xl border border-line" value="advanced">
                <Accordion.Trigger className="flex w-full items-center justify-between px-3 py-3 text-left text-sm font-semibold">
                  {t.advancedSettings}
                  <ChevronDown className="h-4 w-4 transition-transform data-[state=open]:rotate-180" />
                </Accordion.Trigger>
                <Accordion.Content className="border-t border-line p-3">
                  <AdvancedSettings locale={locale} />
                </Accordion.Content>
              </Accordion.Item>
            </Accordion.Root>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
