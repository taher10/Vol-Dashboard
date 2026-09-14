import { InfoHint } from "@/components/info-hint";
import { cn } from "@/lib/utils";

/** Surface for a chart or table.
 *
 * The header used to be an inverted bar (bg-foreground + text-background).
 * That reads as heavy even on light, and on the dark theme it inverted into a
 * glaring near-white band above every card -- the brightest thing on screen
 * sitting above the data it was labelling. It's now a quiet caption separated
 * by a hairline: hierarchy comes from type weight and letter-spacing rather
 * than from a block of contrast, which keeps attention on the numbers. */
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
    <div
      className={cn(
        "overflow-hidden rounded-lg border border-border bg-card",
        "shadow-[0_1px_2px_rgba(0,0,0,0.30)] transition-colors hover:border-border/80",
        className
      )}
    >
      <div className="flex items-center justify-between gap-2 border-b border-border/70 px-3.5 py-2.5">
        <span className="flex items-center gap-1.5 text-[10.5px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
          {title}
          {hint && <InfoHint text={hint} />}
        </span>
        {action}
      </div>
      <div className={cn("p-3.5", bodyClassName)}>{children}</div>
    </div>
  );
}
