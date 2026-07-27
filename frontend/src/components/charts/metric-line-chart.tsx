"use client";

import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { COLOR_GRID, COLOR_TEXT_MUTED } from "@/lib/theme";
import { fmtDate, fmtNum } from "@/lib/format";

export interface MetricSeries {
  symbol: string;
  color: string;
  data: { dte: number; expiration: string; value: number | null }[];
}

interface MetricLineChartProps {
  series: MetricSeries[];
  yLabel: string;
  height?: number;
  referenceLine?: { value: number; label: string };
  onPointClick?: (expiration: string, symbol: string) => void;
  emptyMessage?: string;
}

interface ChartPointDatum {
  dte: number;
  expiration: string;
  value: number | null;
  symbol: string;
}

interface TooltipPayloadEntry {
  dataKey: string;
  name: string;
  value: number;
  color: string;
  payload: ChartPointDatum;
}

function CustomTooltip({ active, payload }: { active?: boolean; payload?: TooltipPayloadEntry[] }) {
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded-md border border-border bg-popover px-3 py-2 text-xs shadow-md">
      {payload.map((p) => (
        <div key={p.dataKey + p.name} className="flex items-center gap-2 py-0.5">
          <span className="size-2 rounded-full" style={{ backgroundColor: p.color }} />
          <span className="text-muted-foreground">{p.name}</span>
          <span className="font-mono font-medium tabular-nums text-popover-foreground">
            {fmtNum(p.value, 3)}
          </span>
          <span className="text-muted-foreground">{fmtDate(p.payload.expiration)}</span>
        </div>
      ))}
    </div>
  );
}

interface DotProps {
  cx?: number;
  cy?: number;
  payload?: ChartPointDatum;
}

function makeDot(color: string, onPointClick?: (expiration: string, symbol: string) => void) {
  function MetricDot({ cx, cy, payload }: DotProps) {
    if (cx == null || cy == null || payload?.value == null) return null;
    return (
      <circle
        cx={cx}
        cy={cy}
        r={3.5}
        fill={color}
        stroke="var(--card)"
        strokeWidth={1.5}
        style={{ cursor: onPointClick ? "pointer" : "default" }}
        onClick={() => onPointClick?.(payload.expiration, payload.symbol)}
      />
    );
  }
  MetricDot.displayName = "MetricDot";
  return MetricDot;
}

export function ChartLegend({ series }: { series: MetricSeries[] }) {
  if (series.length <= 1) return null;
  return (
    <div className="flex items-center gap-3">
      {series.map((s) => (
        <span key={s.symbol} className="flex items-center gap-1.5 text-[11px] font-medium text-background/90">
          <span className="size-2 shrink-0 rounded-full" style={{ backgroundColor: s.color }} />
          {s.symbol}
        </span>
      ))}
    </div>
  );
}

export function MetricLineChart({
  series,
  yLabel,
  height = 260,
  referenceLine,
  onPointClick,
  emptyMessage = "No data available",
}: MetricLineChartProps) {
  const hasData = series.some((s) => s.data.some((d) => d.value !== null && d.value !== undefined));

  if (!hasData) {
    return (
      <div
        className="flex items-center justify-center text-sm text-muted-foreground"
        style={{ height }}
      >
        {emptyMessage}
      </div>
    );
  }

  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart margin={{ top: 8, right: 16, bottom: 4, left: 4 }}>
        <CartesianGrid stroke={COLOR_GRID} strokeDasharray="3 3" vertical={false} />
        <XAxis
          dataKey="dte"
          type="number"
          domain={["dataMin", "dataMax"]}
          tick={{ fontSize: 11, fill: COLOR_TEXT_MUTED }}
          tickFormatter={(v) => `${v}d`}
          allowDuplicatedCategory={false}
        />
        <YAxis
          tick={{ fontSize: 11, fill: COLOR_TEXT_MUTED }}
          width={44}
          label={{ value: yLabel, angle: -90, position: "insideLeft", fontSize: 11, fill: COLOR_TEXT_MUTED }}
        />
        <Tooltip content={<CustomTooltip />} />
        {referenceLine && (
          <ReferenceLine
            y={referenceLine.value}
            stroke={COLOR_TEXT_MUTED}
            strokeDasharray="4 4"
            label={{ value: referenceLine.label, fontSize: 10, fill: COLOR_TEXT_MUTED, position: "insideTopRight" }}
          />
        )}
        {series.map((s) => (
          <Line
            key={s.symbol}
            data={s.data.map((d) => ({ ...d, symbol: s.symbol }))}
            dataKey="value"
            name={s.symbol}
            stroke={s.color}
            strokeWidth={2}
            dot={makeDot(s.color, onPointClick)}
            activeDot={{ r: 5 }}
            connectNulls
            isAnimationActive={false}
          />
        ))}
      </LineChart>
    </ResponsiveContainer>
  );
}
