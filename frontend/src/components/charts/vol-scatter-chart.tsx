"use client";

import { CartesianGrid, ReferenceLine, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis } from "recharts";

import { COLOR_GRID, COLOR_TEXT_MUTED } from "@/lib/theme";
import { fmtNum } from "@/lib/format";

export interface VolScatterPoint {
  symbol: string;
  color: string;
  x: number;
  y: number;
}

interface TooltipPayloadEntry {
  payload: VolScatterPoint;
}

function CustomTooltip({
  active,
  payload,
  xLabel,
  yLabel,
}: {
  active?: boolean;
  payload?: TooltipPayloadEntry[];
  xLabel: string;
  yLabel: string;
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
        {yLabel}
        <span className="font-mono tabular-nums text-popover-foreground">{fmtNum(p.y, 2)}</span>
      </div>
      <div className="flex items-center gap-2 text-muted-foreground">
        {xLabel}
        <span className="font-mono tabular-nums text-popover-foreground">{fmtNum(p.x, 2)}</span>
      </div>
    </div>
  );
}

/** Dot + ticker label, offset to the upper-right so it doesn't sit on top of the point. */
function LabeledDot({ cx, cy, payload }: { cx?: number; cy?: number; payload?: VolScatterPoint }) {
  if (cx == null || cy == null || !payload) return null;
  return (
    <g>
      <circle cx={cx} cy={cy} r={4} fill={payload.color} stroke="var(--card)" strokeWidth={1.5} />
      <text x={cx + 7} y={cy + 3} fontSize={10} fill={COLOR_TEXT_MUTED}>
        {payload.symbol}
      </text>
    </g>
  );
}

export function VolScatterChart({
  points,
  xLabel,
  yLabel,
  aboveLineMeans,
  belowLineMeans,
  height = 300,
  emptyMessage = "Not enough symbols with both values yet.",
}: {
  points: VolScatterPoint[];
  xLabel: string;
  yLabel: string;
  aboveLineMeans: string;
  belowLineMeans: string;
  height?: number;
  emptyMessage?: string;
}) {
  if (points.length === 0) {
    return (
      <div className="flex items-center justify-center text-sm text-muted-foreground" style={{ height }}>
        {emptyMessage}
      </div>
    );
  }

  // Shared domain on both axes (not each axis's own independent min/max) --
  // the whole point of the y=x reference line is that equal values plot on
  // it, which only reads correctly if both axes use the same scale.
  const values = points.flatMap((p) => [p.x, p.y]);
  const dataMin = Math.min(...values);
  const dataMax = Math.max(...values);
  const pad = Math.max((dataMax - dataMin) * 0.15, 1);
  const domainMin = Math.max(0, Math.floor(dataMin - pad));
  const domainMax = Math.ceil(dataMax + pad);

  return (
    <div>
      <ResponsiveContainer width="100%" height={height}>
        <ScatterChart margin={{ top: 8, right: 16, bottom: 8, left: 4 }}>
          <CartesianGrid stroke={COLOR_GRID} strokeDasharray="3 3" />
          <XAxis
            dataKey="x"
            type="number"
            domain={[domainMin, domainMax]}
            tick={{ fontSize: 11, fill: COLOR_TEXT_MUTED }}
            label={{ value: xLabel, position: "insideBottom", offset: -4, fontSize: 11, fill: COLOR_TEXT_MUTED }}
          />
          <YAxis
            dataKey="y"
            type="number"
            domain={[domainMin, domainMax]}
            tick={{ fontSize: 11, fill: COLOR_TEXT_MUTED }}
            width={44}
            label={{ value: yLabel, angle: -90, position: "insideLeft", fontSize: 11, fill: COLOR_TEXT_MUTED }}
          />
          <Tooltip content={<CustomTooltip xLabel={xLabel} yLabel={yLabel} />} cursor={{ strokeDasharray: "3 3" }} />
          <ReferenceLine
            segment={[
              { x: domainMin, y: domainMin },
              { x: domainMax, y: domainMax },
            ]}
            stroke={COLOR_TEXT_MUTED}
            strokeDasharray="4 4"
          />
          <Scatter data={points} shape={LabeledDot} isAnimationActive={false} />
        </ScatterChart>
      </ResponsiveContainer>
      <p className="mt-1 text-center text-[11px] text-muted-foreground">
        Above the dashed line: {aboveLineMeans}. Below: {belowLineMeans}.
      </p>
    </div>
  );
}
