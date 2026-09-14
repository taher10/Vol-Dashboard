"use client";

import { cn } from "@/lib/utils";
import { fmtSigned } from "@/lib/format";

/** A numeric table cell with its magnitude drawn behind it.
 *
 * A 24-row leaderboard of bare digits has to be *read* row by row to find the
 * outliers -- the ranking is in the data but not on the screen. A bar scaled
 * to the column's own extreme makes the shape of the distribution visible at
 * a glance, so the eye finds the two symbols worth looking at before the
 * brain parses a single number. The digits stay, exact and tabular, because
 * the bar is for scanning and the number is for deciding.
 *
 * Signed values grow from the centre so positive and negative read as
 * opposites rather than as "long" and "short"; unsigned ones grow from the
 * left. `max` is the largest absolute value in the column, passed in by the
 * caller because a cell can't see its own column. */
export function BarCell({
  value,
  max,
  signed = true,
  digits = 0,
  format,
  className,
}: {
  value: number | null;
  max: number;
  signed?: boolean;
  digits?: number;
  format?: (v: number) => string;
  className?: string;
}) {
  if (value === null || Number.isNaN(value)) {
    return <span className="text-muted-foreground">—</span>;
  }

  // A zero-width column (every value identical, or all zero) would divide by
  // zero; render the text alone rather than a misleading full-width bar.
  const pct = max > 0 ? Math.min(Math.abs(value) / max, 1) * 100 : 0;
  const positive = value >= 0;
  const label = format ? format(value) : signed ? fmtSigned(value, digits) : value.toFixed(digits);

  return (
    <span className="relative flex items-center justify-end gap-2">
      <span
        aria-hidden
        className="pointer-events-none absolute inset-y-[3px] flex w-[58%] min-w-8"
        style={{ right: 0, justifyContent: signed ? "center" : "flex-start" }}
      >
        <span className="relative h-full w-full">
          <span
            className={cn("absolute top-0 h-full rounded-[2px] opacity-[0.22]", positive ? "bg-pos" : "bg-neg")}
            style={
              signed
                ? { left: positive ? "50%" : undefined, right: positive ? undefined : "50%", width: `${pct / 2}%` }
                : { left: 0, width: `${pct}%` }
            }
          />
          {signed && <span className="absolute inset-y-0 left-1/2 w-px bg-border" />}
        </span>
      </span>
      <span className={cn("relative z-10 font-mono tabular-nums", positive ? "text-pos" : "text-neg", className)}>
        {label}
      </span>
    </span>
  );
}
