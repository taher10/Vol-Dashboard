"use client";

import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { COLOR_CALL, COLOR_GRID, COLOR_PUT, COLOR_TEXT_MUTED } from "@/lib/theme";
import { fmtNum } from "@/lib/format";
import type { ContractRow } from "@/lib/api";

function CustomTooltip({ active, payload }: { active?: boolean; payload?: { payload: ContractRow }[] }) {
  if (!active || !payload?.length) return null;
  const p = payload[0].payload;
  return (
    <div className="rounded-md border border-border bg-popover px-3 py-2 text-xs shadow-md">
      <div className="font-medium text-popover-foreground">
        #{p.rank} {p.optionType} {p.strikePrice}
      </div>
      <div className="text-muted-foreground">score {fmtNum(p.composite_score, 1)}</div>
      <div className="text-muted-foreground">exp {p.expiration?.slice(0, 10)}</div>
    </div>
  );
}

export function CandidateBarChart({ rows, height = 280 }: { rows: ContractRow[]; height?: number }) {
  if (rows.length === 0) {
    return (
      <div className="flex items-center justify-center text-sm text-muted-foreground" style={{ height }}>
        No candidates match the current filters.
      </div>
    );
  }

  const data = rows.map((r) => ({
    ...r,
    label: `${r.optionType[0]} ${r.strikePrice}`,
  }));

  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={data} margin={{ top: 8, right: 16, bottom: 24, left: 4 }}>
        <CartesianGrid stroke={COLOR_GRID} strokeDasharray="3 3" vertical={false} />
        <XAxis
          dataKey="label"
          tick={{ fontSize: 10, fill: COLOR_TEXT_MUTED }}
          interval={0}
          angle={-35}
          textAnchor="end"
          height={50}
        />
        <YAxis
          tick={{ fontSize: 11, fill: COLOR_TEXT_MUTED }}
          width={36}
          domain={[0, 100]}
          label={{ value: "Score", angle: -90, position: "insideLeft", fontSize: 11, fill: COLOR_TEXT_MUTED }}
        />
        <Tooltip content={<CustomTooltip />} />
        <Bar dataKey="composite_score" radius={[3, 3, 0, 0]} isAnimationActive={false}>
          {data.map((d) => (
            <Cell key={d.rank} fill={d.optionType === "CALL" ? COLOR_CALL : COLOR_PUT} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}
