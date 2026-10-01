type ResultCardProps = {
  detail?: string;
  title: string;
  value: string;
};

export function ResultCard({ detail, title, value }: ResultCardProps) {
  return (
    <div className="rounded-lg border border-line bg-surface p-3">
      <div className="text-xs text-muted">{title}</div>
      <div className="mt-1 text-xl font-semibold text-ink">{value}</div>
      {detail && <div className="mt-1 text-xs text-muted">{detail}</div>}
    </div>
  );
}
