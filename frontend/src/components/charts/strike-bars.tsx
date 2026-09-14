"use client";

import { Bar, BarChart, CartesianGrid, Cell, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { fmtInt, fmtNum } from "@/lib/format";
import { COLOR_GRID, COLOR_TEXT_MUTED } from "@/lib/theme";

/** Calls and puts by strike, as bars.
 *
 * Bars rather than lines on purpose. Open interest is a discrete quantity
 * sitting at a specific listed strike -- there is no value "between" 355 and
 * 360, and a line implies a continuum that doesn't exist. It also makes the
 * thing traders actually look for, a single strike carrying far more
 * positioning than its neighbours, visible instead of smoothed into a slope.
 *
 * Peak strike is outlined rather than recoloured so it reads as "this one"
 * without changing what the colour already encodes (call vs put). */

export interface StrikeDatum {
  strike: number;
  call: number | null;
  put: number | null;
}

function Payload({
  active,
  payload,
  label,
  unitDigits,
}: {
  active?: boolean;
  payload?: { dataKey: string; value: number; color: string }[];
  label?: number;
  unitDigits: number;
}) {
  if (!active || !payload?.length) return null;
  const total = payload.reduce((s, p) => s + (p.value ?? 0), 0);
  return (
    <div className="rounded-md border border-border bg-popover px-3 py-2 text-xs shadow-md">
      <div className="mb-1 font-medium text-popover-foreground">Strike {fmtNum(label, 2)}</div>
      {payload.map((p) => (
        <div key={p.dataKey} className="flex items-center gap-2 py-0.5">
          <span className="size-2 rounded-full" style={{ backgroundColor: p.color }} />
          <span className="capitalize text-muted-foreground">{p.dataKey}</span>
          <span className="ml-auto font-mono tabular-nums text-popover-foreground">
            {unitDigits === 0 ? fmtInt(p.value) : fmtNum(p.value, unitDigits)}
          </span>
        </div>
      ))}
      <div className="mt-1 flex gap-2 border-t border-border pt-1 text-muted-foreground">
        <span>total</span>
        <span className="ml-auto font-mono tabular-nums">
          {unitDigits === 0 ? fmtInt(total) : fmtNum(total, unitDigits)}
        </span>
      </div>
    </div>
  );
}

export function StrikeBars({
  data,
  spot,
  peakStrike,
  unitDigits = 0,
  height = 260,
}: {
  data: StrikeDatum[];
  spot: number | null;
  peakStrike: number | null;
  unitDigits?: number;
  height?: number;
}) {
  if (data.length === 0) {
    return <div className="py-8 text-center text-sm text-muted-foreground">No data for this expiration.</div>;
  }

  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={data} margin={{ top: 8, right: 12, bottom: 4, left: 0 }} barGap={0}>
        <CartesianGrid stroke={COLOR_GRID} strokeDasharray="3 3" vertical={false} />
        <XAxis
          dataKey="strike"
          type="number"
          domain={["dataMin", "dataMax"]}
          tick={{ fontSize: 11, fill: COLOR_TEXT_MUTED }}
          tickFormatter={(v: number) => fmtNum(v, 0)}
        />
        <YAxis
          tick={{ fontSize: 11, fill: COLOR_TEXT_MUTED }}
          width={52}
          tickFormatter={(v: number) => (unitDigits === 0 ? fmtInt(v) : fmtNum(v, unitDigits))}
        />
        <Tooltip content={<Payload unitDigits={unitDigits} />} cursor={{ fill: "rgba(255,255,255,0.04)" }} />
        {spot != null && (
          <ReferenceLine
            x={spot}
            stroke={COLOR_TEXT_MUTED}
            strokeDasharray="4 4"
            label={{ value: "spot", position: "top", fill: COLOR_TEXT_MUTED, fontSize: 10 }}
          />
        )}
        <Bar dataKey="call" fill="var(--chart-1)" isAnimationActive={false} radius={[1, 1, 0, 0]}>
          {data.map((d) => (
            <Cell
              key={d.strike}
              stroke={peakStrike === d.strike ? "var(--foreground)" : undefined}
              strokeWidth={peakStrike === d.strike ? 1 : 0}
            />
          ))}
        </Bar>
        <Bar dataKey="put" fill="var(--neg)" isAnimationActive={false} radius={[1, 1, 0, 0]}>
          {data.map((d) => (
            <Cell
              key={d.strike}
              stroke={peakStrike === d.strike ? "var(--foreground)" : undefined}
              strokeWidth={peakStrike === d.strike ? 1 : 0}
            />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}
