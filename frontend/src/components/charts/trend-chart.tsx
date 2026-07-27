"use client";

import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { COLOR_GRID, COLOR_TEXT_MUTED } from "@/lib/theme";
import { fmtDate, fmtNum } from "@/lib/format";

export interface TrendPoint {
  date: string;
  value: number | null;
}

interface TooltipPayloadEntry {
  value: number;
  payload: TrendPoint;
}

function CustomTooltip({ active, payload }: { active?: boolean; payload?: TooltipPayloadEntry[] }) {
  if (!active || !payload?.length) return null;
  const p = payload[0];
  return (
    <div className="rounded-md border border-border bg-popover px-3 py-2 text-xs shadow-md">
      <div className="text-muted-foreground">{fmtDate(p.payload.date)}</div>
      <div className="font-mono font-medium tabular-nums text-popover-foreground">{fmtNum(p.value, 3)}</div>
    </div>
  );
}

export function TrendChart({
  data,
  yLabel,
  color = "#2a78d6",
  height = 240,
}: {
  data: TrendPoint[];
  yLabel: string;
  color?: string;
  height?: number;
}) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={data} margin={{ top: 8, right: 16, bottom: 4, left: 4 }}>
        <CartesianGrid stroke={COLOR_GRID} strokeDasharray="3 3" vertical={false} />
        <XAxis
          dataKey="date"
          tick={{ fontSize: 11, fill: COLOR_TEXT_MUTED }}
          tickFormatter={(v) => fmtDate(v)}
        />
        <YAxis
          tick={{ fontSize: 11, fill: COLOR_TEXT_MUTED }}
          width={44}
          domain={["auto", "auto"]}
          label={{ value: yLabel, angle: -90, position: "insideLeft", fontSize: 11, fill: COLOR_TEXT_MUTED }}
        />
        <Tooltip content={<CustomTooltip />} />
        <Line
          dataKey="value"
          stroke={color}
          strokeWidth={2}
          dot={{ r: 3, fill: color }}
          connectNulls
          isAnimationActive={false}
        />
      </LineChart>
    </ResponsiveContainer>
  );
}
