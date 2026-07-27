import { cn } from "@/lib/utils";

export function ChartCard({
  title,
  action,
  children,
  className,
  bodyClassName,
}: {
  title: string;
  action?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
  bodyClassName?: string;
}) {
  return (
    <div className={cn("overflow-hidden rounded-lg border border-border bg-card shadow-sm", className)}>
      <div className="flex items-center justify-between bg-foreground px-3 py-2">
        <span className="text-[11px] font-semibold uppercase tracking-wider text-background">{title}</span>
        {action}
      </div>
      <div className={cn("p-3", bodyClassName)}>{children}</div>
    </div>
  );
}
