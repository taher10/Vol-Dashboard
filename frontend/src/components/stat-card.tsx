import { InfoHint } from "@/components/info-hint";
import { cn } from "@/lib/utils";

export function StatCard({
  label,
  value,
  sub,
  hint,
  accent,
  className,
}: {
  label: string;
  value: string;
  sub?: string;
  hint?: string;
  accent?: "good" | "critical" | "neutral";
  className?: string;
}) {
  const accentColor =
    accent === "good" ? "text-pos" : accent === "critical" ? "text-neg" : "text-foreground";
  return (
    <div className={cn("flex flex-col gap-1 rounded-lg border border-border bg-card px-4 py-3", className)}>
      <span className="flex items-center gap-1 text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
        {label}
        {hint && <InfoHint text={hint} />}
      </span>
      <span className={cn("font-mono text-xl font-semibold tabular-nums", accentColor)}>{value}</span>
      {sub && <span className="text-xs text-muted-foreground">{sub}</span>}
    </div>
  );
}
