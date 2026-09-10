"use client";

import { Bar, BarChart, CartesianGrid, Cell, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { COLOR_GRID, COLOR_TEXT_MUTED } from "@/lib/theme";
import { fmtSigned } from "@/lib/format";

export interface VolBarRow {
  symbol: string;
  color: string;
  value: number;
}

interface TooltipPayloadEntry {
  payload: VolBarRow;
}

function CustomTooltip({
  active,
  payload,
  valueLabel,
  formatValue,
}: {
  active?: boolean;
  payload?: TooltipPayloadEntry[];
  valueLabel: string;
  formatValue: (v: number) => string;
}) {
  if (!active || !payload?.length) return null;
  const p = payload[0].payload;
  return (
    <div className="rounded-md border border-border bg-popover px-3 py-2 text-xs shadow-md">
      <div className="mb-1 flex items-center gap-1.5 font-medium text-popover-foreground">
        <span className="size-2 rounded-full" style={{ backgroundColor: p.color }} />
        {p.symbol}
      </div>
      <div className="flex items-center gap-2 text-muted-foreground">
        {valueLabel}
        <span className="font-mono tabular-nums text-popover-foreground">{formatValue(p.value)}</span>
      </div>
    </div>
  );
}

/** Horizontal leaderboard bar -- one bar per symbol, sorted descending by
 * value, colored by each symbol's theme color. Rows with a null value are
 * dropped by the caller before this ever renders (a symbol with no signal
 * yet shouldn't get a fabricated zero-length bar). */
export function VolBarChart({
  rows,
  valueLabel,
  height,
  emptyMessage = "No symbols have this data yet.",
  referenceValue,
  formatValue = (v) => fmtSigned(v, 2),
}: {
  rows: VolBarRow[];
  valueLabel: string;
  height?: number;
  emptyMessage?: string;
  /** Explicit reference line (e.g. PCR's parity point at 1) -- falls back to
   * a 0-line only when the data itself goes negative (VRP/richness-style
   * charts) if this isn't given. */
  referenceValue?: number;
  formatValue?: (v: number) => string;
}) {
  if (rows.length === 0) {
    return (
      <div className="flex items-center justify-center text-sm text-muted-foreground" style={{ height: height ?? 200 }}>
        {emptyMessage}
      </div>
    );
  }

  const sorted = [...rows].sort((a, b) => b.value - a.value);
  const chartHeight = height ?? Math.max(sorted.length * 26 + 24, 120);
  const hasNegative = sorted.some((r) => r.value < 0);
  const lineAt = referenceValue ?? (hasNegative ? 0 : null);

  return (
    <ResponsiveContainer width="100%" height={chartHeight}>
      <BarChart data={sorted} layout="vertical" margin={{ top: 4, right: 24, bottom: 4, left: 4 }}>
        <CartesianGrid stroke={COLOR_GRID} strokeDasharray="3 3" horizontal={false} />
        <XAxis type="number" tick={{ fontSize: 11, fill: COLOR_TEXT_MUTED }} />
        <YAxis
          dataKey="symbol"
          type="category"
          tick={{ fontSize: 11, fill: COLOR_TEXT_MUTED }}
          width={48}
          interval={0}
        />
        <Tooltip
          content={<CustomTooltip valueLabel={valueLabel} formatValue={formatValue} />}
          cursor={{ fill: "rgba(0,0,0,0.03)" }}
        />
        {lineAt !== null && <ReferenceLine x={lineAt} stroke={COLOR_TEXT_MUTED} />}
        <Bar dataKey="value" isAnimationActive={false} radius={2}>
          {sorted.map((r) => (
            <Cell key={r.symbol} fill={r.color} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}
