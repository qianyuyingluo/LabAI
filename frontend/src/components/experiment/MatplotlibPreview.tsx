import type { Locale } from "@/lib/i18n";

type MatplotlibPreviewProps = {
  locale: Locale;
};

export function MatplotlibPreview({ locale }: MatplotlibPreviewProps) {
  const isZh = locale === "zh";

  return (
    <div className="space-y-3">
      <div className="rounded-lg border border-line bg-surface p-4">
        <div className="flex h-48 items-center justify-center rounded-md border border-dashed border-line bg-panel text-center text-sm text-muted">
          {isZh
            ? "等待 Python / matplotlib 导出的 fit_result.png"
            : "Waiting for fit_result.png exported by Python / matplotlib"}
        </div>
      </div>
      <pre className="overflow-x-auto rounded-lg border border-line bg-panel p-3 text-xs leading-5">
        <code>{`fig, ax = plt.subplots(figsize=(6, 4), dpi=160)
ax.scatter(x_data, y_data, label="data")
ax.plot(x_data, fitted, label="fit")
fig.tight_layout()
fig.savefig("fit_result.png")`}</code>
      </pre>
    </div>
  );
}
