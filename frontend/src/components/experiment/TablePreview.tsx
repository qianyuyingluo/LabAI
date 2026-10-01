import type { Locale } from "@/lib/i18n";

const rows = [
  ["0.42", "0.18", "正常"],
  ["0.56", "0.44", "正常"],
  ["0.71", "0.91", "复核"],
  ["0.83", "1.36", "正常"],
];

export function TablePreview({ locale }: { locale: Locale }) {
  const headers =
    locale === "zh"
      ? ["电压 V", "电流 mA", "状态"]
      : ["Voltage V", "Current mA", "Status"];
  const localizedRows =
    locale === "zh"
      ? rows
      : rows.map(([voltage, current, status]) => [
          voltage,
          current,
          status === "复核" ? "Review" : "Normal",
        ]);

  return (
    <div className="overflow-hidden rounded-lg border border-line">
      <table className="w-full text-left text-sm">
        <thead className="bg-panel text-xs text-muted">
          <tr>
            {headers.map((header) => (
              <th className="px-3 py-2" key={header}>
                {header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {localizedRows.map((row) => (
            <tr className="border-t border-line" key={row.join("-")}>
              {row.map((cell) => (
                <td className="px-3 py-2" key={cell}>
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
