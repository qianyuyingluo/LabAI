"use client";

import * as Tabs from "@radix-ui/react-tabs";
import { BarChart3, FileText, Table2, TrendingUp, X } from "lucide-react";
import { copy, type Locale } from "@/lib/i18n";
import { MatplotlibPreview } from "@/components/experiment/MatplotlibPreview";
import { ReportPreview } from "@/components/experiment/ReportPreview";
import { ResultCard } from "@/components/experiment/ResultCard";
import { TablePreview } from "@/components/experiment/TablePreview";
import { cn } from "@/lib/utils";

type ResultPanelProps = {
  locale: Locale;
  open: boolean;
  onOpenChange: (open: boolean) => void;
};

function PanelContent({
  locale,
  onClose,
}: {
  locale: Locale;
  onClose: () => void;
}) {
  const t = copy[locale];
  const tabs = [
    { id: "data", label: t.data, icon: Table2 },
    { id: "chart", label: t.plot, icon: BarChart3 },
    { id: "fit", label: t.fit, icon: TrendingUp },
    { id: "report", label: t.report, icon: FileText },
  ];

  return (
    <div className="flex h-full flex-col">
      <div className="flex h-14 shrink-0 items-center justify-between border-b border-line px-4">
        <h2 className="text-sm font-semibold">{t.resultPreview}</h2>
        <button
          aria-label={t.closeResult}
          className="rounded-lg p-2 text-muted hover:bg-panel hover:text-ink"
          onClick={onClose}
          type="button"
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      <Tabs.Root className="min-h-0 flex-1" defaultValue="data">
        <Tabs.List className="grid grid-cols-4 border-b border-line px-2 pt-2">
          {tabs.map((tab) => {
            const Icon = tab.icon;
            return (
              <Tabs.Trigger
                className="flex items-center justify-center gap-1.5 rounded-t-lg px-2 py-2 text-xs text-muted data-[state=active]:bg-panel data-[state=active]:text-ink"
                key={tab.id}
                value={tab.id}
              >
                <Icon className="h-3.5 w-3.5" />
                {tab.label}
              </Tabs.Trigger>
            );
          })}
        </Tabs.List>

        <div className="app-scrollbar h-[calc(100%-45px)] overflow-y-auto p-4">
          <Tabs.Content className="space-y-3 outline-none" value="data">
            <TablePreview locale={locale} />
          </Tabs.Content>
          <Tabs.Content className="space-y-3 outline-none" value="chart">
            <MatplotlibPreview locale={locale} />
          </Tabs.Content>
          <Tabs.Content className="space-y-3 outline-none" value="fit">
            <div className="grid grid-cols-2 gap-3">
              <ResultCard
                detail={locale === "zh" ? "线性拟合" : "Linear fit"}
                title={locale === "zh" ? "斜率 a" : "Slope a"}
                value="2.184"
              />
              <ResultCard
                detail={locale === "zh" ? "截距" : "Intercept"}
                title="b"
                value="-0.037"
              />
              <ResultCard
                detail={locale === "zh" ? "拟合优度" : "Fit quality"}
                title="R²"
                value="0.992"
              />
              <ResultCard
                detail={locale === "zh" ? "需复核" : "Needs review"}
                title={locale === "zh" ? "异常点" : "Outliers"}
                value="3"
              />
            </div>
          </Tabs.Content>
          <Tabs.Content className="outline-none" value="report">
            <ReportPreview locale={locale} />
          </Tabs.Content>
        </div>
      </Tabs.Root>
    </div>
  );
}

export function ResultPanel({ locale, onOpenChange, open }: ResultPanelProps) {
  return (
    <>
      <aside
        className={cn(
          "hidden w-[370px] shrink-0 border-l border-line bg-surface lg:block",
          !open && "lg:hidden",
        )}
      >
        <PanelContent locale={locale} onClose={() => onOpenChange(false)} />
      </aside>

      {open && (
        <div className="fixed inset-0 z-40 lg:hidden">
          <button
            aria-label={copy[locale].closeOverlay}
            className="absolute inset-0 bg-black/20"
            onClick={() => onOpenChange(false)}
            type="button"
          />
          <div className="absolute inset-x-0 bottom-0 h-[78vh] rounded-t-2xl border-t border-line bg-surface shadow-xl">
            <PanelContent locale={locale} onClose={() => onOpenChange(false)} />
          </div>
        </div>
      )}
    </>
  );
}
