"use client";

import { CartesianGrid, Legend, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import type { StrikeProfileRow } from "@/lib/api";
import { fmtNum } from "@/lib/format";
import { COLOR_CALL, COLOR_GRID, COLOR_LINE_DEFAULT, COLOR_PUT, COLOR_TEXT_MUTED } from "@/lib/theme";

export type StrikeProfileMetric = "iv" | "delta" | "gammaOi";

interface ChartDatum {
  strike: number;
  a: number | null;
  b: number | null;
}

interface TooltipPayloadEntry {
  dataKey: string;
  name: string;
  value: number;
  color: string;
}

function CustomTooltip({
  active,
  payload,
  label,
}: {
  active?: boolean;
  payload?: TooltipPayloadEntry[];
  label?: number;
}) {
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded-md border border-border bg-popover px-3 py-2 text-xs shadow-md">
      <div className="mb-1 font-medium text-popover-foreground">Strike {fmtNum(label, 2)}</div>
      {payload.map((p) => (
        <div key={p.dataKey} className="flex items-center gap-2 py-0.5">
          <span className="size-2 rounded-full" style={{ backgroundColor: p.color }} />
          <span className="text-muted-foreground">{p.name}</span>
          <span className="font-mono font-medium tabular-nums text-popover-foreground">{fmtNum(p.value, 3)}</span>
        </div>
      ))}
    </div>
  );
}

/** IV is one theoretical value per strike in this data source (identical
 * for calls and puts at the same strike -- verified against the raw
 * chain), so it's a single curve, not two. Gamma is likewise ~identical
 * call vs put by put-call parity, so the informative two-sided cut is
 * gamma weighted by each side's own open interest, not raw gamma twice. */
function toChartData(rows: StrikeProfileRow[], metric: StrikeProfileMetric): { data: ChartDatum[]; aLabel: string; bLabel: string | null } {
  if (metric === "iv") {
    return {
      data: rows.map((r) => ({ strike: r.strike, a: r.call_iv ?? r.put_iv, b: null })),
      aLabel: "Implied Vol",
      bLabel: null,
    };
  }
  if (metric === "delta") {
    return {
      data: rows.map((r) => ({ strike: r.strike, a: r.call_delta, b: r.put_delta })),
      aLabel: "Call Delta",
      bLabel: "Put Delta",
    };
  }
  return {
    data: rows.map((r) => ({
      strike: r.strike,
      a: r.call_gamma != null && r.call_oi != null ? r.call_gamma * r.call_oi : null,
      b: r.put_gamma != null && r.put_oi != null ? r.put_gamma * r.put_oi : null,
    })),
    aLabel: "Call Gamma × OI",
    bLabel: "Put Gamma × OI",
  };
}

export function StrikeProfileChart({
  rows,
  metric,
  underlyingPrice,
  height = 320,
  emptyMessage = "No strike data for this expiration.",
}: {
  rows: StrikeProfileRow[];
  metric: StrikeProfileMetric;
  underlyingPrice: number | null;
  height?: number;
  emptyMessage?: string;
}) {
  if (rows.length === 0) {
    return (
      <div className="flex items-center justify-center text-sm text-muted-foreground" style={{ height }}>
        {emptyMessage}
      </div>
    );
  }

  const { data, aLabel, bLabel } = toChartData(rows, metric);
  const colorA = metric === "iv" ? COLOR_LINE_DEFAULT : COLOR_CALL;

  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={data} margin={{ top: 8, right: 16, bottom: 4, left: 4 }}>
        <CartesianGrid stroke={COLOR_GRID} strokeDasharray="3 3" vertical={false} />
        <XAxis
          dataKey="strike"
          type="number"
          domain={["dataMin", "dataMax"]}
          tick={{ fontSize: 11, fill: COLOR_TEXT_MUTED }}
          label={{ value: "Strike", position: "insideBottom", offset: -4, fontSize: 11, fill: COLOR_TEXT_MUTED }}
        />
        <YAxis tick={{ fontSize: 11, fill: COLOR_TEXT_MUTED }} width={52} />
        <Tooltip content={<CustomTooltip />} />
        {bLabel && <Legend wrapperStyle={{ fontSize: 11 }} />}
        {underlyingPrice != null && (
          <ReferenceLine
            x={underlyingPrice}
            stroke={COLOR_TEXT_MUTED}
            strokeDasharray="4 4"
            label={{ value: "Spot", fontSize: 10, fill: COLOR_TEXT_MUTED, position: "top" }}
          />
        )}
        <Line
          dataKey="a"
          name={aLabel}
          stroke={colorA}
          strokeWidth={2}
          dot={false}
          connectNulls
          isAnimationActive={false}
        />
        {bLabel && (
          <Line
            dataKey="b"
            name={bLabel}
            stroke={COLOR_PUT}
            strokeWidth={2}
            dot={false}
            connectNulls
            isAnimationActive={false}
          />
        )}
      </LineChart>
    </ResponsiveContainer>
  );
}
