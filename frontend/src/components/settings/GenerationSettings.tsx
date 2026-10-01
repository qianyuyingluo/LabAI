"use client";

import * as Slider from "@radix-ui/react-slider";
import * as Switch from "@radix-ui/react-switch";
import { useState } from "react";
import { copy, type Locale } from "@/lib/i18n";

function SettingSwitch({
  defaultChecked,
  label,
}: {
  defaultChecked?: boolean;
  label: string;
}) {
  return (
    <div className="flex items-center justify-between gap-3 rounded-lg border border-line px-3 py-2">
      <span className="text-sm text-ink">{label}</span>
      <Switch.Root
        className="relative h-5 w-9 rounded-full bg-line data-[state=checked]:bg-accent"
        defaultChecked={defaultChecked}
      >
        <Switch.Thumb className="block h-4 w-4 translate-x-0.5 rounded-full bg-surface transition-transform data-[state=checked]:translate-x-[18px]" />
      </Switch.Root>
    </div>
  );
}

function SettingSlider({
  defaultValue,
  label,
  max,
  min = 0,
  step,
}: {
  defaultValue: number;
  label: string;
  max: number;
  min?: number;
  step: number;
}) {
  const [value, setValue] = useState(defaultValue);

  return (
    <div className="rounded-lg border border-line px-3 py-3">
      <div className="mb-3 flex items-center justify-between text-sm">
        <span>{label}</span>
        <span className="text-muted">{value}</span>
      </div>
      <Slider.Root
        className="relative flex h-5 touch-none select-none items-center"
        max={max}
        min={min}
        onValueChange={([next]) => setValue(next)}
        step={step}
        value={[value]}
      >
        <Slider.Track className="relative h-1.5 grow rounded-full bg-line">
          <Slider.Range className="absolute h-full rounded-full bg-accent" />
        </Slider.Track>
        <Slider.Thumb
          aria-label={label}
          className="block h-4 w-4 rounded-full border border-line bg-surface shadow-sm outline-none focus:ring-2 focus:ring-accent/30"
        />
      </Slider.Root>
    </div>
  );
}

export function GenerationSettings({ locale }: { locale: Locale }) {
  const t = copy[locale];

  return (
    <div className="space-y-3">
      <SettingSlider defaultValue={0.4} label="temperature" max={1.5} step={0.1} />
      <SettingSlider defaultValue={4096} label="max tokens" max={32000} step={512} />
      <SettingSlider defaultValue={0.9} label="top_p" max={1} step={0.05} />
      <SettingSwitch defaultChecked label={t.streamEnabled} />
    </div>
  );
}

export function ExperimentProcessingSettings({ locale }: { locale: Locale }) {
  const t = copy[locale];

  return (
    <div className="space-y-2">
      <SettingSwitch defaultChecked label={t.autoFit} />
      <SettingSwitch defaultChecked label={t.autoReport} />
      <SettingSwitch defaultChecked label={t.autoError} />
    </div>
  );
}
