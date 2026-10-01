"use client";

import * as Select from "@radix-ui/react-select";
import { Bot, ChevronDown, Cpu, Image as ImageIcon, Loader2 } from "lucide-react";
import { copy, type Locale } from "@/lib/i18n";
import type { ModelProfile } from "@/lib/types";

type ModelSelectorProps = {
  locale: Locale;
  loading?: boolean;
  models: ModelProfile[];
  value: string;
  onValueChange: (value: string) => void;
};

export function ModelSelector({
  loading = false,
  locale,
  models,
  onValueChange,
  value,
}: ModelSelectorProps) {
  const selected = models.find((option) => option.id === value);
  const t = copy[locale];
  const emptyText = locale === "zh" ? "未配置模型" : "No model";
  const loadingText = locale === "zh" ? "加载模型" : "Loading";

  return (
    <Select.Root onValueChange={onValueChange} value={value}>
      <Select.Trigger
        aria-label={t.modelSelector}
        className="flex h-9 min-w-0 items-center gap-2 rounded-lg px-2.5 text-sm text-ink outline-none hover:bg-panel disabled:cursor-not-allowed disabled:opacity-60"
        data-testid="model-selector"
        disabled={loading || models.length === 0}
      >
        {loading ? (
          <Loader2 className="h-4 w-4 shrink-0 animate-spin text-muted" />
        ) : (
          <Cpu className="h-4 w-4 shrink-0 text-muted" />
        )}
        <span className="hidden max-w-32 truncate md:inline">
          {loading ? loadingText : selected?.name ?? emptyText}
        </span>
        <ChevronDown className="h-4 w-4 shrink-0 text-muted" />
      </Select.Trigger>
      <Select.Portal>
        <Select.Content
          align="end"
          className="z-50 w-80 overflow-hidden rounded-xl border border-line bg-surface p-1 text-sm shadow-lg"
          position="popper"
          sideOffset={8}
        >
          <Select.Viewport>
            {models.map((option) => (
              <Select.Item
                className="cursor-pointer rounded-lg px-3 py-2 outline-none hover:bg-panel data-[state=checked]:bg-panel"
                key={option.id}
                value={option.id}
              >
                <Select.ItemText>
                  <span className="flex min-w-0 items-center gap-2">
                    <Bot className="h-4 w-4 shrink-0 text-muted" />
                    <span className="truncate font-medium">{option.name}</span>
                    {option.supports_vision && (
                      <ImageIcon className="h-3.5 w-3.5 shrink-0 text-accent" />
                    )}
                  </span>
                  <span className="mt-1 block truncate text-xs text-muted">
                    {option.model_name || "model unset"}
                    {option.base_url ? ` · ${option.base_url}` : ""}
                  </span>
                </Select.ItemText>
              </Select.Item>
            ))}
            {models.length === 0 && (
              <div className="px-3 py-2 text-sm text-muted">{emptyText}</div>
            )}
          </Select.Viewport>
        </Select.Content>
      </Select.Portal>
    </Select.Root>
  );
}
