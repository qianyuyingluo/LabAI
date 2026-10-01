"use client";

import type { Locale } from "@/lib/i18n";
import { StreamingMarkdown } from "@/components/chat/StreamingMarkdown";

const reports: Record<Locale, string> = {
  zh: `## 实验报告摘要

本次数据处理完成了空值检查、异常点标记和线性拟合。拟合残差集中在零附近，说明模型可以解释主要趋势。

- 建议保留 A-03 样本，但在误差分析中说明复核原因。
- 后续报告应附原始数据表、拟合曲线和参数置信区间。
`,
  en: `## Report Summary

The data workflow covers missing-value checks, outlier marking, and linear fitting. Residuals are concentrated near zero, so the model explains the main trend.

- Keep sample A-03, but explain the review flag in the uncertainty analysis.
- Attach the raw data table, fitted plot, and parameter confidence intervals in the final report.
`,
};

export function ReportPreview({ locale }: { locale: Locale }) {
  return <StreamingMarkdown content={reports[locale]} />;
}
