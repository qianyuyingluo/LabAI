"use client";

import * as Checkbox from "@radix-ui/react-checkbox";
import { Check } from "lucide-react";
import { copy, type Locale } from "@/lib/i18n";

function DebugCheckbox({ label }: { label: string }) {
  return (
    <label className="flex items-center gap-2 rounded-lg border border-line px-3 py-2 text-sm">
      <Checkbox.Root className="flex h-4 w-4 items-center justify-center rounded border border-line data-[state=checked]:border-accent data-[state=checked]:bg-accent">
        <Checkbox.Indicator>
          <Check className="h-3 w-3 text-accent-ink" />
        </Checkbox.Indicator>
      </Checkbox.Root>
      {label}
    </label>
  );
}

export function AdvancedSettings({ locale }: { locale: Locale }) {
  const t = copy[locale];

  return (
    <div className="space-y-3">
      <DebugCheckbox label={t.debugMode} />
      <DebugCheckbox label={t.rawJson} />
      <DebugCheckbox label={t.performanceDebug} />
    </div>
  );
}
