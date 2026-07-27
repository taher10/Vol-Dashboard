"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import { SiteHeader } from "@/components/site-header";
import { ChartCard } from "@/components/chart-card";
import { ChartLegend, MetricLineChart, type MetricSeries } from "@/components/charts/metric-line-chart";
import { RichnessTable } from "@/components/richness-table";
import { StatCard } from "@/components/stat-card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  api,
  ApiError,
  type CurvaturePoint,
  type IVRank,
  type OverviewResponse,
  type SkewPoint,
  type TermStructurePoint,
  type VrpPoint,
} from "@/lib/api";
import { fmtDateTime, fmtNum } from "@/lib/format";
import { useSettingsStore } from "@/lib/store";

export default function OverviewPage() {
  const symbols = useSettingsStore((s) => s.symbols);
  const dteRange = useSettingsStore((s) => s.dteRange);
  const refreshNonce = useSettingsStore((s) => s.refreshNonce);
  const router = useRouter();

  const [data, setData] = useState<OverviewResponse | null>(null);
  const [ivRank, setIvRank] = useState<IVRank | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    // Resetting loading/error before a new fetch is the standard data-fetching-in-effect
    // pattern; the cancelled flag below still guards against out-of-order responses.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setLoading(true);
    setError(null);
    api
      .overview(symbols, dteRange[0], dteRange[1])
      .then((res) => {
        if (cancelled) return;
        setData(res);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err instanceof ApiError ? err.message : "Failed to load overview data.");
        setData(null);
      })
      .finally(() => !cancelled && setLoading(false));

    api
      .ivRank(symbols[0] ?? "SPX")
      .then((r) => !cancelled && setIvRank(r))
      .catch(() => !cancelled && setIvRank(null));

    return () => {
      cancelled = true;
    };
  }, [symbols, dteRange, refreshNonce]);

  const handlePointClick = (expiration: string, symbol: string) => {
    router.push(`/expiry/${symbol}?expiration=${encodeURIComponent(expiration)}`);
  };

  type AnyMetricPoint = TermStructurePoint | SkewPoint | CurvaturePoint | VrpPoint;

  function buildSeries(metric: "term_structure" | "skew" | "curvature" | "vrp", valueKey: string): MetricSeries[] {
    if (!data) return [];
    return Object.entries(data.symbols).map(([symbol, sym]) => ({
      symbol,
      color: sym.color,
      data: ((sym[metric] ?? []) as AnyMetricPoint[]).map((pt) => ({
        dte: pt.dte,
        expiration: pt.expiration,
        value: (pt as unknown as Record<string, number | null>)[valueKey] ?? null,
      })),
    }));
  }

  return (
    <>
      <SiteHeader title="Overview" />
      <main className="flex-1 overflow-y-auto p-4 md:p-6">
        {error && (
          <div className="mb-4 rounded-md border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive">
            {error}
          </div>
        )}

        {data?.missing_symbols && data.missing_symbols.length > 0 && (
          <div className="mb-4 rounded-md border border-border bg-muted px-4 py-2 text-xs text-muted-foreground">
            No saved snapshot yet for: {data.missing_symbols.join(", ")}
          </div>
        )}

        <div className="mb-4 flex flex-wrap items-center gap-3">
          {loading && !data ? (
            <Skeleton className="h-16 w-full max-w-2xl" />
          ) : (
            <>
              {data?.takeaway && (
                <div className="flex-1 rounded-lg border border-border bg-accent/60 px-4 py-3 text-sm text-accent-foreground">
                  {data.takeaway}
                </div>
              )}
              {ivRank && (
                <StatCard
                  label="IV Rank (30d)"
                  value={fmtNum(ivRank.iv_rank, 0)}
                  sub={`Percentile ${fmtNum(ivRank.iv_percentile, 0)} · IV ${fmtNum(ivRank.current_iv, 1)}`}
                />
              )}
            </>
          )}
        </div>

        {data && (
          <p className="mb-4 text-xs text-muted-foreground">
            As of {fmtDateTime(data.as_of)} · showing {dteRange[0]}–{dteRange[1]} DTE
          </p>
        )}

        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          {(() => {
            const termSeries = buildSeries("term_structure", "atm_iv");
            const skewSeries = buildSeries("skew", "skew");
            const curvatureSeries = buildSeries("curvature", "curvature");
            const vrpSeries = buildSeries("vrp", "vrp");
            return (
              <>
                <ChartCard
                  title="Term Structure"
                  hint="At-the-money implied volatility across expiries — how the market prices volatility differently by time to expiration."
                  action={<ChartLegend series={termSeries} />}
                >
                  {loading && !data ? (
                    <Skeleton className="h-64 w-full" />
                  ) : (
                    <MetricLineChart series={termSeries} yLabel="ATM IV" onPointClick={handlePointClick} />
                  )}
                </ChartCard>

                <ChartCard
                  title="Skew (25Δ put − call)"
                  hint="IV(25Δ put) − IV(25Δ call). Positive means puts are priced richer than calls the same distance from the money."
                  action={<ChartLegend series={skewSeries} />}
                >
                  {loading && !data ? (
                    <Skeleton className="h-64 w-full" />
                  ) : (
                    <MetricLineChart series={skewSeries} yLabel="Skew" />
                  )}
                </ChartCard>

                <ChartCard
                  title="Curvature"
                  hint="Butterfly: average of 25Δ put/call IV minus ATM IV — how much the smile's wings are priced up relative to its middle."
                  action={<ChartLegend series={curvatureSeries} />}
                >
                  {loading && !data ? (
                    <Skeleton className="h-64 w-full" />
                  ) : (
                    <MetricLineChart series={curvatureSeries} yLabel="Curvature" />
                  )}
                </ChartCard>

                <ChartCard
                  title="Variance Risk Premium"
                  hint="ATM implied volatility minus realized volatility — how much more (or less) the options market is pricing in than what actually happened."
                  action={<ChartLegend series={vrpSeries} />}
                >
                  {loading && !data ? (
                    <Skeleton className="h-64 w-full" />
                  ) : (
                    <MetricLineChart
                      series={vrpSeries}
                      yLabel="VRP"
                      emptyMessage="VRP unavailable for the selected symbols (needs price history at save time)."
                    />
                  )}
                </ChartCard>
              </>
            );
          })()}
        </div>

        <div className="mt-4">
          <ChartCard title="Expiry Richness" bodyClassName="p-0 pt-3">
            {loading && !data ? (
              <Skeleton className="m-3 h-48 w-full" />
            ) : (
              <div className="px-3 pb-3">
                <RichnessTable
                  rows={data?.expiry_scores ?? []}
                  onSelectExpiration={(expiration) => handlePointClick(expiration, data!.primary)}
                />
              </div>
            )}
          </ChartCard>
        </div>
      </main>
    </>
  );
}
