"use client";

import {
  CartesianGrid,
  Line,
  ComposedChart,
  ReferenceDot,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { COLOR_CALL, COLOR_GRID, COLOR_HIGHLIGHT, COLOR_PUT, COLOR_TEXT_MUTED } from "@/lib/theme";
import { fmtNum } from "@/lib/format";
import type { SmilePoint } from "@/lib/api";

interface SmileChartProps {
  points: SmilePoint[];
  highlight?: { delta: number; impliedVolatility: number; optionType: string; strikePrice: number } | null;
  height?: number;
}

function signedDelta(p: { delta: number; optionType: string }) {
  return p.optionType === "PUT" ? -Math.abs(p.delta) : Math.abs(p.delta);
}

interface SmileTooltipPayload {
  payload: SmilePoint & { x: number };
}

function CustomTooltip({ active, payload }: { active?: boolean; payload?: SmileTooltipPayload[] }) {
  if (!active || !payload?.length) return null;
  const p = payload[0].payload;
  return (
    <div className="rounded-md border border-border bg-popover px-3 py-2 text-xs shadow-md">
      <div className="font-medium text-popover-foreground">
        {p.optionType} {p.strikePrice}
      </div>
      <div className="text-muted-foreground">delta {fmtNum(p.delta, 3)}</div>
      <div className="text-muted-foreground">IV {fmtNum(p.impliedVolatility, 3)}</div>
    </div>
  );
}

export function SmileChart({ points, highlight, height = 320 }: SmileChartProps) {
  const valid = points.filter((p) => p.delta != null && p.impliedVolatility != null);
  if (valid.length === 0) {
    return (
      <div className="flex items-center justify-center text-sm text-muted-foreground" style={{ height }}>
        No data available
      </div>
    );
  }

  const calls = valid
    .filter((p) => p.optionType === "CALL")
    .map((p) => ({ ...p, x: signedDelta(p) }))
    .sort((a, b) => a.x - b.x);
  const puts = valid
    .filter((p) => p.optionType === "PUT")
    .map((p) => ({ ...p, x: signedDelta(p) }))
    .sort((a, b) => a.x - b.x);

  const highlightX = highlight ? signedDelta(highlight) : null;

  return (
    <ResponsiveContainer width="100%" height={height}>
      <ComposedChart margin={{ top: 8, right: 16, bottom: 24, left: 4 }}>
        <CartesianGrid stroke={COLOR_GRID} strokeDasharray="3 3" />
        <XAxis
          dataKey="x"
          type="number"
          domain={[-1, 1]}
          tick={{ fontSize: 11, fill: COLOR_TEXT_MUTED }}
          label={{ value: "Delta (put ← → call)", position: "insideBottom", offset: -8, fontSize: 11, fill: COLOR_TEXT_MUTED }}
        />
        <YAxis
          dataKey="impliedVolatility"
          tick={{ fontSize: 11, fill: COLOR_TEXT_MUTED }}
          width={44}
          label={{ value: "IV (%)", angle: -90, position: "insideLeft", fontSize: 11, fill: COLOR_TEXT_MUTED }}
        />
        <Tooltip content={<CustomTooltip />} />
        <Line data={puts} dataKey="impliedVolatility" name="PUT" stroke={COLOR_PUT} strokeWidth={2} dot={{ r: 3, fill: COLOR_PUT }} isAnimationActive={false} />
        <Line data={calls} dataKey="impliedVolatility" name="CALL" stroke={COLOR_CALL} strokeWidth={2} dot={{ r: 3, fill: COLOR_CALL }} isAnimationActive={false} />
        {highlight && highlightX !== null && (
          <ReferenceDot
            x={highlightX}
            y={highlight.impliedVolatility}
            r={8}
            fill={COLOR_HIGHLIGHT}
            stroke="var(--card)"
            strokeWidth={2}
          />
        )}
      </ComposedChart>
    </ResponsiveContainer>
  );
}
