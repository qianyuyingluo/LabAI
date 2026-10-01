"use client";

import * as Select from "@radix-ui/react-select";
import { BookOpenText, ChevronDown, Languages, Menu, PanelRight, Settings, Zap } from "lucide-react";
import { copy, localeLabels, type Locale } from "@/lib/i18n";
import type { Attachment, ModelProfile } from "@/lib/types";
import { UploadButton } from "@/components/experiment/UploadButton";
import { ModelSelector } from "@/components/settings/ModelSelector";

type TopBarProps = {
  currentModel: string;
  locale: Locale;
  modelLoading?: boolean;
  models: ModelProfile[];
  onFilesAdded: (files: Attachment[]) => void;
  onOpenMobileSidebar: () => void;
  onOpenPlanPrompts: () => void;
  onOpenSettings: () => void;
  onRunStressTest: () => void;
  onToggleResultPanel: () => void;
  resultPanelOpen: boolean;
  setLocale: (value: Locale) => void;
  setCurrentModel: (value: string) => void;
};

export function TopBar({
  currentModel,
  locale,
  modelLoading = false,
  models,
  onFilesAdded,
  onOpenMobileSidebar,
  onOpenPlanPrompts,
  onOpenSettings,
  onRunStressTest,
  onToggleResultPanel,
  resultPanelOpen,
  setLocale,
  setCurrentModel,
}: TopBarProps) {
  const t = copy[locale];

  return (
    <header className="flex h-14 shrink-0 items-center justify-between border-b border-line bg-surface px-2 sm:px-4">
      <div className="flex min-w-0 items-center gap-1 sm:gap-2">
        <button
          aria-label={t.openSidebar}
          className="rounded-lg p-2 text-muted hover:bg-panel hover:text-ink lg:hidden"
          onClick={onOpenMobileSidebar}
          type="button"
        >
          <Menu className="h-5 w-5" />
        </button>
        <div className="truncate px-1 text-sm font-semibold sm:text-base">
          {t.appName}
        </div>
      </div>

      <div className="flex shrink-0 items-center gap-1">
        <UploadButton locale={locale} onFilesAdded={onFilesAdded} />
        <ModelSelector
          locale={locale}
          loading={modelLoading}
          models={models}
          onValueChange={setCurrentModel}
          value={currentModel}
        />
        <LanguageSelector locale={locale} setLocale={setLocale} />
        <button
          aria-label="View Plan Mode prompts"
          className="hidden h-9 items-center gap-2 rounded-lg px-2.5 text-sm text-ink hover:bg-panel md:flex"
          onClick={onOpenPlanPrompts}
          type="button"
        >
          <BookOpenText className="h-4 w-4 text-muted" />
          Prompts
        </button>
        <button
          aria-label={t.runStressTest}
          className="hidden h-9 items-center gap-2 rounded-lg px-2.5 text-sm text-ink hover:bg-panel md:flex"
          data-testid="stress-test-button"
          onClick={onRunStressTest}
          type="button"
        >
          <Zap className="h-4 w-4 text-muted" />
          {t.stressTest}
        </button>
        <button
          aria-pressed={resultPanelOpen}
          className="flex h-9 items-center gap-2 rounded-lg px-2.5 text-sm text-ink hover:bg-panel"
          onClick={onToggleResultPanel}
          type="button"
        >
          <PanelRight className="h-4 w-4 text-muted" />
          <span className="hidden md:inline">{t.result}</span>
        </button>
        <button
          aria-label={t.openSettings}
          className="flex h-9 w-9 items-center justify-center rounded-lg text-muted hover:bg-panel hover:text-ink"
          data-testid="settings-button"
          onClick={onOpenSettings}
          type="button"
        >
          <Settings className="h-4 w-4" />
        </button>
      </div>
    </header>
  );
}

function LanguageSelector({
  locale,
  setLocale,
}: {
  locale: Locale;
  setLocale: (value: Locale) => void;
}) {
  const t = copy[locale];

  return (
    <Select.Root onValueChange={(value) => setLocale(value as Locale)} value={locale}>
      <Select.Trigger
        aria-label={t.language}
        className="flex h-9 items-center gap-2 rounded-lg px-2.5 text-sm text-ink outline-none hover:bg-panel"
        data-testid="language-selector"
      >
        <Languages className="h-4 w-4 shrink-0 text-muted" />
        <span className="hidden md:inline">{localeLabels[locale]}</span>
        <ChevronDown className="h-4 w-4 shrink-0 text-muted" />
      </Select.Trigger>
      <Select.Portal>
        <Select.Content
          align="end"
          className="z-50 overflow-hidden rounded-xl border border-line bg-surface p-1 text-sm shadow-lg"
          position="popper"
          sideOffset={8}
        >
          <Select.Viewport>
            {(["zh", "en"] as const).map((option) => (
              <Select.Item
                className="cursor-pointer rounded-lg px-3 py-2 outline-none hover:bg-panel data-[state=checked]:bg-panel"
                key={option}
                value={option}
              >
                <Select.ItemText>{localeLabels[option]}</Select.ItemText>
              </Select.Item>
            ))}
          </Select.Viewport>
        </Select.Content>
      </Select.Portal>
    </Select.Root>
  );
}
