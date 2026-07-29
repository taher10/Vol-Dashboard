"use client";

import {
  Area,
  CartesianGrid,
  ComposedChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { COLOR_GRID, COLOR_HIGHLIGHT, COLOR_TEXT_MUTED, STATUS_CRITICAL, STATUS_GOOD } from "@/lib/theme";
import { fmtNum } from "@/lib/format";
import type { PayoffPoint } from "@/lib/api";

function CustomTooltip({ active, payload }: { active?: boolean; payload?: { payload: PayoffPoint }[] }) {
  if (!active || !payload?.length) return null;
  const p = payload[0].payload;
  return (
    <div className="rounded-md border border-border bg-popover px-3 py-2 text-xs shadow-md">
      <div className="font-medium text-popover-foreground">Underlying ${fmtNum(p.underlying, 2)}</div>
      <div className={p.pnl >= 0 ? "text-[#0b5c0b]" : "text-[#8f2323]"}>P/L {fmtNum(p.pnl, 2)}</div>
    </div>
  );
}

export function PayoffChart({
  payoff,
  breakevens,
  spot,
  height = 280,
}: {
  payoff: PayoffPoint[];
  breakevens: number[];
  spot?: number | null;
  height?: number;
}) {
  if (payoff.length === 0) {
    return (
      <div className="flex items-center justify-center text-sm text-muted-foreground" style={{ height }}>
        No payoff data
      </div>
    );
  }

  const values = payoff.map((p) => p.pnl);
  const minY = Math.min(...values);
  const maxY = Math.max(...values);
  // Fraction of the gradient's top-to-bottom span (y1=0 -> maxY, y2=1 -> minY)
  // at which P/L crosses zero, so the fill can split green above / red below
  // the actual zero line rather than the chart's visual midpoint.
  const zeroOffset = maxY === minY ? 0.5 : Math.min(1, Math.max(0, maxY / (maxY - minY)));

  return (
    <ResponsiveContainer width="100%" height={height}>
      <ComposedChart data={payoff} margin={{ top: 20, right: 16, bottom: 24, left: 4 }}>
        <defs>
          <linearGradient id="payoffFill" x1="0" y1="0" x2="0" y2="1">
            <stop offset={zeroOffset} stopColor={STATUS_GOOD} stopOpacity={0.25} />
            <stop offset={zeroOffset} stopColor={STATUS_CRITICAL} stopOpacity={0.25} />
          </linearGradient>
        </defs>
        <CartesianGrid stroke={COLOR_GRID} strokeDasharray="3 3" />
        <XAxis
          dataKey="underlying"
          type="number"
          domain={["dataMin", "dataMax"]}
          tick={{ fontSize: 11, fill: COLOR_TEXT_MUTED }}
          tickFormatter={(v: number) => fmtNum(v, 0)}
          label={{
            value: "Underlying price at expiry",
            position: "insideBottom",
            offset: -8,
            fontSize: 11,
            fill: COLOR_TEXT_MUTED,
          }}
        />
        <YAxis
          dataKey="pnl"
          tick={{ fontSize: 11, fill: COLOR_TEXT_MUTED }}
          width={52}
          label={{ value: "P/L", angle: -90, position: "insideLeft", fontSize: 11, fill: COLOR_TEXT_MUTED }}
        />
        <Tooltip content={<CustomTooltip />} />
        <ReferenceLine y={0} stroke={COLOR_TEXT_MUTED} strokeDasharray="3 3" />
        {breakevens.map((be) => (
          <ReferenceLine
            key={be}
            x={be}
            stroke={COLOR_TEXT_MUTED}
            strokeDasharray="4 2"
            label={{ value: `BE ${fmtNum(be, 1)}`, fontSize: 10, fill: COLOR_TEXT_MUTED, position: "top" }}
          />
        ))}
        {spot != null && (
          <ReferenceLine
            x={spot}
            stroke={COLOR_HIGHLIGHT}
            strokeWidth={1.5}
            label={{ value: "Spot", fontSize: 10, fill: COLOR_HIGHLIGHT, position: "insideTopRight" }}
          />
        )}
        <Area
          type="linear"
          dataKey="pnl"
          stroke={COLOR_HIGHLIGHT}
          strokeWidth={2}
          fill="url(#payoffFill)"
          isAnimationActive={false}
          dot={false}
        />
      </ComposedChart>
    </ResponsiveContainer>
  );
}
