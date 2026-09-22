"use client";

import { useEffect, useMemo, useState } from "react";
import { CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { ChartCard } from "@/components/chart-card";
import { PageIntro } from "@/components/page-intro";
import { SiteHeader } from "@/components/site-header";
import { Skeleton } from "@/components/ui/skeleton";
import { VolSurfaceHeatmap } from "@/components/charts/vol-surface-heatmap";
import { api, ApiError, type SurfaceResponse } from "@/lib/api";
import { fmtDateTime, fmtNum } from "@/lib/format";
import { cn } from "@/lib/utils";
import { COLOR_GRID, COLOR_TEXT_MUTED } from "@/lib/theme";
import { primarySymbol, useSettingsStore } from "@/lib/store";

export default function SurfacePage() {
  const symbols = useSettingsStore((s) => s.symbols);
  const symbol = primarySymbol(symbols);

  const [data, setData] = useState<SurfaceResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedDte, setSelectedDte] = useState<number | null>(null);
  const [compareDate, setCompareDate] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- standard fetch-reset pattern, guarded by `cancelled`
    setLoading(true);
    setError(null);
    api
      .surface(symbol, compareDate ?? undefined)
      .then((res) => {
        if (cancelled) return;
        setData(res);
        // Default to the nearest expiry with real data rather than whatever
        // was selected for the previous symbol.
        setSelectedDte(res.expirations[0]?.dte ?? null);
      })
      .catch(
        (err) =>
          !cancelled &&
          setError(err instanceof ApiError ? `Failed to load surface: ${err.message}` : "Failed to load the surface.")
      )
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [symbol, compareDate]);

  useEffect(() => {
    setCompareDate(null);
  }, [symbol]);

  const smile = useMemo(() => {
    if (!data || selectedDte === null) return [];
    return data.cells
      .filter((c) => c.dte === selectedDte)
      .sort((a, b) => a.moneyness - b.moneyness)
      .map((c) => ({ moneyness: c.moneyness * 100, iv: c.iv, side: c.side }));
  }, [data, selectedDte]);

  // The two wings at equal distance from spot: the single number that says
  // how lopsided this expiry is priced, and the reason "skew" exists as a
  // summary at all.
  const wings = useMemo(() => {
    if (smile.length === 0) return null;
    const put = smile.find((p) => p.moneyness <= -9.5 && p.moneyness >= -10.5);
    const call = smile.find((p) => p.moneyness >= 9.5 && p.moneyness <= 10.5);
    if (!put || !call) return null;
    return { put: put.iv, call: call.iv, diff: put.iv - call.iv };
  }, [smile]);

  const comparison = data?.comparison ?? null;
  const changeAbsMax = useMemo(() => {
    if (!comparison) return 0;
    return Math.max(Math.abs(comparison.change_min ?? 0), Math.abs(comparison.change_max ?? 0));
  }, [comparison]);

  // In change mode the grid is the constant-maturity buckets the comparison
  // was computed on, not the raw expirations -- those are what can be
  // compared like-for-like across two dates.
  const heatmapExpirations = useMemo(() => {
    if (!comparison) return data?.expirations ?? [];
    return [...new Set(comparison.cells.map((c) => c.dte))]
      .sort((a, b) => a - b)
      .map((dte) => ({ dte, expiration: `${dte}d constant maturity` }));
  }, [comparison, data]);

  const heatmapMoneyness = useMemo(() => {
    if (!comparison) return data?.moneyness ?? [];
    return [...new Set(comparison.cells.map((c) => c.moneyness))].sort((a, b) => a - b);
  }, [comparison, data]);

  const selectedExpiration = data?.expirations.find((e) => e.dte === selectedDte);

  return (
    <>
      <SiteHeader title="Vol Surface" />
      <main className="flex-1 overflow-y-auto p-4 md:p-6">
        <PageIntro
          summary={
            <>
              Implied vol across every strike and expiration for {symbol}. Click a row to read its smile below.
            </>
          }
        >
          <p className="mb-2">
            This is the raw surface that Term Structure and skew are summaries of. A skew number tells you puts are
            richer; only the surface tells you whether that&apos;s a smooth smirk, a kink at one strike, or a single
            stale quote dragging the fit.
          </p>
          <p className="mb-2">
            Cells are keyed by <span className="text-foreground">moneyness</span> (strike ÷ spot − 1) rather than
            strike, so a 1-week and a 6-month expiry line up on one axis. Only out-of-the-money quotes are used —
            puts below spot, calls above. That&apos;s the market convention: in-the-money options trade thinly and
            their quoted IVs are dominated by intrinsic value and wide spreads, which would put a spurious step at
            the money.
          </p>
          <p>
            Empty cells are left empty. A gap means no contract was quoted at that strike and expiry — filling it by
            interpolation would invent a volatility the market never printed.
          </p>
        </PageIntro>

        {error && (
          <div className="mb-4 rounded-md border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive">
            {error}
          </div>
        )}

        {loading || !data ? (
          <Skeleton className="h-96 w-full" />
        ) : data.cells.length === 0 ? (
          <div className="rounded-lg border border-border bg-card px-4 py-8 text-center text-sm text-muted-foreground">
            No quoted contracts within ±25% of spot for {symbol}.
          </div>
        ) : (
          <>
            <div className="mb-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-muted-foreground">
              <span>
                Spot <span className="font-mono text-foreground">{fmtNum(data.spot, 2)}</span>
              </span>
              <span>
                {data.expirations.length} expirations · {data.cells.length} quoted cells
              </span>
              <span>Snapshot {fmtDateTime(data.as_of)}</span>
            </div>

            <div className="mb-3 flex flex-wrap items-center gap-2">
              <div className="inline-flex overflow-hidden rounded-md border border-border">
                <button
                  type="button"
                  onClick={() => setCompareDate(null)}
                  className={cn(
                    "px-2.5 py-1 text-xs transition-colors",
                    !compareDate ? "bg-foreground text-background" : "bg-card hover:bg-accent"
                  )}
                >
                  Levels
                </button>
                <button
                  type="button"
                  onClick={() => setCompareDate(data.available_compare_dates[0] ?? null)}
                  disabled={data.available_compare_dates.length === 0}
                  className={cn(
                    "px-2.5 py-1 text-xs transition-colors disabled:opacity-40",
                    compareDate ? "bg-foreground text-background" : "bg-card hover:bg-accent"
                  )}
                >
                  Change vs…
                </button>
              </div>

              {compareDate && (
                <>
                  <select
                    value={compareDate}
                    onChange={(e) => setCompareDate(e.target.value)}
                    className="rounded-md border border-border bg-background px-2 py-1 font-mono text-xs"
                  >
                    {data.available_compare_dates.map((d) => (
                      <option key={d} value={d}>
                        {d}
                      </option>
                    ))}
                  </select>
                  {comparison && (
                    <span className="text-[11px] text-muted-foreground">
                      {/* The real gap, stated plainly. The pipeline's outages mean
                          the nearest stored snapshot is often far older than "a
                          week ago", and calling an 18-day move a weekly one would
                          understate it by more than double. */}
                      <span className="text-foreground">
                        {comparison.days_elapsed} {comparison.days_elapsed === 1 ? "day" : "days"}
                      </span>{" "}
                      elapsed
                      {comparison.spot_change_pct !== null && (
                        <>
                          {" · spot "}
                          <span className={comparison.spot_change_pct >= 0 ? "text-pos" : "text-neg"}>
                            {comparison.spot_change_pct >= 0 ? "+" : ""}
                            {fmtNum(comparison.spot_change_pct, 1)}%
                          </span>
                        </>
                      )}
                      {" · "}
                      {comparison.cells.length} comparable cells
                    </span>
                  )}
                </>
              )}
            </div>

            <ChartCard
              title="Implied Volatility Surface"
              hint="Moneyness across, expiry down, colour is implied vol. Rendered flat rather than as a rotatable 3D plot on purpose: in 3D the wing you care about is usually hidden behind the one you don't, and comparing two points means judging height by eye."
              className="mb-4"
            >
              <VolSurfaceHeatmap
                cells={comparison ? comparison.cells : data.cells}
                expirations={heatmapExpirations}
                moneyness={heatmapMoneyness}
                ivMin={data.iv_min ?? 0}
                ivMax={data.iv_max ?? 1}
                selectedDte={comparison ? null : selectedDte}
                onSelectDte={setSelectedDte}
                mode={comparison ? "change" : "level"}
                changeAbsMax={changeAbsMax}
              />
            </ChartCard>

            <ChartCard
              title={
                selectedExpiration
                  ? `Smile — ${selectedExpiration.expiration} (${selectedExpiration.dte}d)`
                  : "Smile"
              }
              hint="One slice through the surface. A downward-sloping left side means downside puts are bid above upside calls -- the classic equity smirk, and what the skew summary compresses into a single number."
              action={
                wings && (
                  <span className="font-mono text-[11px] tabular-nums text-muted-foreground">
                    10% put {fmtNum(wings.put, 1)} · call {fmtNum(wings.call, 1)} ·{" "}
                    <span className={wings.diff >= 0 ? "text-neg" : "text-pos"}>
                      {wings.diff >= 0 ? "puts richer" : "calls richer"} {fmtNum(Math.abs(wings.diff), 1)}
                    </span>
                  </span>
                )
              }
            >
              {smile.length === 0 ? (
                <div className="py-8 text-center text-sm text-muted-foreground">
                  Select an expiration in the surface above.
                </div>
              ) : (
                <ResponsiveContainer width="100%" height={260}>
                  <LineChart data={smile} margin={{ top: 8, right: 16, bottom: 4, left: 0 }}>
                    <CartesianGrid stroke={COLOR_GRID} strokeDasharray="3 3" />
                    <XAxis
                      dataKey="moneyness"
                      type="number"
                      domain={["dataMin", "dataMax"]}
                      tick={{ fontSize: 11, fill: COLOR_TEXT_MUTED }}
                      tickFormatter={(v: number) => `${v.toFixed(0)}%`}
                      label={{
                        value: "Moneyness (strike vs spot)",
                        position: "insideBottom",
                        offset: -2,
                        fill: COLOR_TEXT_MUTED,
                        fontSize: 11,
                      }}
                    />
                    <YAxis
                      tick={{ fontSize: 11, fill: COLOR_TEXT_MUTED }}
                      width={44}
                      label={{ value: "IV", angle: -90, position: "insideLeft", fill: COLOR_TEXT_MUTED, fontSize: 11 }}
                    />
                    <Tooltip
                      contentStyle={{
                        background: "var(--popover)",
                        border: "1px solid var(--border)",
                        borderRadius: 6,
                        fontSize: 12,
                      }}
                      labelFormatter={(v) => `${Number(v).toFixed(1)}% from spot`}
                      formatter={(value, _name, item) => {
                        const side = (item?.payload as { side?: string } | undefined)?.side ?? "";
                        return [`${Number(value).toFixed(1)}  (${side})`, "IV"];
                      }}
                    />
                    <ReferenceLine x={0} stroke={COLOR_TEXT_MUTED} strokeDasharray="4 4" />
                    <Line
                      type="monotone"
                      dataKey="iv"
                      stroke="var(--chart-1)"
                      strokeWidth={2}
                      dot={{ r: 2 }}
                      isAnimationActive={false}
                    />
                  </LineChart>
                </ResponsiveContainer>
              )}
            </ChartCard>
          </>
        )}
      </main>
    </>
  );
}
