import { InfoHint } from "@/components/info-hint";
import { cn } from "@/lib/utils";

export function ChartCard({
  title,
  hint,
  action,
  children,
  className,
  bodyClassName,
}: {
  title: string;
  hint?: string;
  action?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
  bodyClassName?: string;
}) {
  return (
    <div className={cn("overflow-hidden rounded-lg border border-border bg-card shadow-sm", className)}>
      <div className="flex items-center justify-between gap-2 bg-foreground px-3 py-2">
        <span className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-background">
          {title}
          {hint && <InfoHint text={hint} className="text-background" />}
        </span>
        {action}
      </div>
      <div className={cn("p-3", bodyClassName)}>{children}</div>
    </div>
  );
}
