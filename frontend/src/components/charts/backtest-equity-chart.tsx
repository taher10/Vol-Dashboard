"use client";

import { useState } from "react";
import { CartesianGrid, Legend, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { COLOR_GRID, COLOR_TEXT_MUTED } from "@/lib/theme";
import { fmtDate, fmtNum } from "@/lib/format";
import type { EquityPoint } from "@/lib/api";

export interface EquitySeries {
  key: "a" | "b";
  label: string;
  color: string;
  points: EquityPoint[];
}

interface MergedRow {
  date: string;
  a: number | null;
  b: number | null;
  aPoint?: EquityPoint;
  bPoint?: EquityPoint;
}

interface TooltipPayloadEntry {
  dataKey: "a" | "b";
  value: number;
  color: string;
}

function CustomTooltip({
  active,
  payload,
  label,
  seriesByKey,
}: {
  active?: boolean;
  payload?: TooltipPayloadEntry[];
  label?: string;
  seriesByKey: Record<string, EquitySeries>;
}) {
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded-md border border-border bg-popover px-3 py-2 text-xs shadow-md">
      <div className="mb-1 text-muted-foreground">{fmtDate(label)}</div>
      {payload.map((p) => (
        <div key={p.dataKey} className="flex items-center gap-2 py-0.5">
          <span className="size-2 rounded-full" style={{ backgroundColor: p.color }} />
          <span className="text-muted-foreground">{seriesByKey[p.dataKey]?.label ?? p.dataKey}</span>
          <span className="font-mono font-medium tabular-nums text-popover-foreground">
            {fmtNum(p.value, 2)}/share
          </span>
        </div>
      ))}
    </div>
  );
}

/** One or two P&L-per-share equity curves overlaid on a shared date axis --
 * built for Backtest's compare mode, but renders fine with just series A
 * alone too. Series can have different dates (each structure can skip
 * different gap days), so points are merged onto the union of both series'
 * dates rather than assuming a shared X array; connectNulls bridges the
 * gaps. Legend entries are clickable (toggle a series' visibility) and each
 * point is clickable (reports back via onPointClick, e.g. to pin a day's
 * detail below the chart) -- both real interactions, not just decoration. */
export function BacktestEquityChart({
  series,
  height = 280,
  onPointClick,
}: {
  series: EquitySeries[];
  height?: number;
  onPointClick?: (seriesKey: "a" | "b", point: EquityPoint) => void;
}) {
  const [hidden, setHidden] = useState<Set<string>>(new Set());

  if (series.every((s) => s.points.length === 0)) {
    return (
      <div className="flex items-center justify-center text-sm text-muted-foreground" style={{ height }}>
        No equity curve to plot yet.
      </div>
    );
  }

  const seriesByKey = Object.fromEntries(series.map((s) => [s.key, s])) as Record<string, EquitySeries>;

  const byDate = new Map<string, MergedRow>();
  for (const s of series) {
    for (const p of s.points) {
      const row = byDate.get(p.date) ?? { date: p.date, a: null, b: null };
      row[s.key] = p.pnl_per_share;
      row[`${s.key}Point` as "aPoint" | "bPoint"] = p;
      byDate.set(p.date, row);
    }
  }
  const rows = [...byDate.values()].sort((a, b) => a.date.localeCompare(b.date));

  function toggle(key: string) {
    setHidden((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={rows} margin={{ top: 8, right: 16, bottom: 4, left: 4 }}>
        <CartesianGrid stroke={COLOR_GRID} strokeDasharray="3 3" vertical={false} />
        <XAxis dataKey="date" tick={{ fontSize: 11, fill: COLOR_TEXT_MUTED }} tickFormatter={(v) => fmtDate(v)} />
        <YAxis
          tick={{ fontSize: 11, fill: COLOR_TEXT_MUTED }}
          width={52}
          domain={["auto", "auto"]}
          label={{ value: "P&L / share", angle: -90, position: "insideLeft", fontSize: 11, fill: COLOR_TEXT_MUTED }}
        />
        <ReferenceLine y={0} stroke={COLOR_TEXT_MUTED} strokeDasharray="3 3" />
        <Tooltip content={<CustomTooltip seriesByKey={seriesByKey} />} />
        {series.length > 1 && (
          <Legend
            onClick={(e) => e.dataKey && toggle(String(e.dataKey))}
            wrapperStyle={{ fontSize: 11, cursor: "pointer" }}
            formatter={(value, entry) => (
              <span style={{ opacity: hidden.has(String(entry.dataKey)) ? 0.4 : 1 }}>{value}</span>
            )}
          />
        )}
        {series.map((s) => (
          <Line
            key={s.key}
            dataKey={s.key}
            name={s.label}
            stroke={s.color}
            strokeWidth={2}
            hide={hidden.has(s.key)}
            connectNulls
            isAnimationActive={false}
            dot={(props: { cx?: number; cy?: number; payload?: MergedRow; index?: number }) => {
              const { cx, cy, payload, index } = props;
              const point = payload?.[`${s.key}Point` as "aPoint" | "bPoint"];
              if (cx == null || cy == null || !point) return <g key={`${s.key}-${index}`} />;
              return (
                <circle
                  key={`${s.key}-${index}`}
                  cx={cx}
                  cy={cy}
                  r={3.5}
                  fill={s.color}
                  stroke="var(--card)"
                  strokeWidth={1.5}
                  style={{ cursor: onPointClick ? "pointer" : "default" }}
                  onClick={() => onPointClick?.(s.key, point)}
                />
              );
            }}
            activeDot={{ r: 5 }}
          />
        ))}
      </LineChart>
    </ResponsiveContainer>
  );
}
