"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";

import { SiteHeader } from "@/components/site-header";
import { ChartCard } from "@/components/chart-card";
import { StatCard } from "@/components/stat-card";
import { TrendChart, type TrendPoint } from "@/components/charts/trend-chart";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { api, type HistoryMetric, type IVRank, type MetricSeriesPoint, type PriceBar, type ZScore } from "@/lib/api";
import { fmtNum } from "@/lib/format";
import { COLOR_CALL } from "@/lib/theme";

const METRIC_OPTIONS: { value: HistoryMetric; label: string }[] = [
  { value: "atm_iv", label: "ATM IV" },
  { value: "skew", label: "Skew" },
  { value: "curvature", label: "Curvature" },
  { value: "realized_vol", label: "Realized Vol" },
  { value: "vrp", label: "VRP" },
];

const MIN_POINTS = 2;

function toTrendPoints(series: MetricSeriesPoint[], key: string): TrendPoint[] {
  return series.map((row) => ({
    date: row.snapshot_date,
    value: (row[key] as number | null | undefined) ?? null,
  }));
}

export default function HistoryPage() {
  const params = useParams<{ symbol: string }>();
  const symbol = params.symbol.toUpperCase();

  const [metric, setMetric] = useState<HistoryMetric>("atm_iv");
  const [metricPoints, setMetricPoints] = useState<TrendPoint[]>([]);
  const [pricePoints, setPricePoints] = useState<TrendPoint[]>([]);
  const [ivRank, setIvRank] = useState<IVRank | null>(null);
  const [zscore, setZscore] = useState<ZScore | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- standard fetch-reset pattern, guarded by `cancelled`
    setLoading(true);
    setError(null);

    Promise.all([
      api.metricSeries(symbol, metric),
      api.priceSeries(symbol),
      api.ivRank(symbol),
      api.zscore(symbol, metric === "atm_iv" ? "skew" : metric),
    ])
      .then(([metricRes, priceRes, ivRankRes, zscoreRes]) => {
        if (cancelled) return;
        setMetricPoints(toTrendPoints(metricRes.series, metric));
        setPricePoints(priceRes.prices.map((p: PriceBar) => ({ date: p.price_date, value: p.close })));
        setIvRank(ivRankRes);
        setZscore(zscoreRes);
      })
      .catch(() => !cancelled && setError("Failed to load history data."))
      .finally(() => !cancelled && setLoading(false));

    return () => {
      cancelled = true;
    };
  }, [symbol, metric]);

  const metricLabel = METRIC_OPTIONS.find((m) => m.value === metric)?.label ?? metric;
  const sessionCount = metricPoints.length;

  return (
    <>
      <SiteHeader title={`History — ${symbol}`} />
      <main className="flex-1 overflow-y-auto p-4 md:p-6">
        {error && (
          <div className="mb-4 rounded-md border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive">
            {error}
          </div>
        )}

        <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-4">
          <StatCard
            label="IV Rank (30d)"
            value={ivRank ? fmtNum(ivRank.iv_rank, 0) : "—"}
            sub={ivRank ? `Percentile ${fmtNum(ivRank.iv_percentile, 0)}` : "Needs 2+ sessions"}
          />
          <StatCard
            label="IV (current)"
            value={ivRank ? fmtNum(ivRank.current_iv, 2) : "—"}
            sub={ivRank ? `Range ${fmtNum(ivRank.lookback_low, 1)}–${fmtNum(ivRank.lookback_high, 1)}` : undefined}
          />
          <StatCard
            label={`${metric === "atm_iv" ? "Skew" : metricLabel} z-score`}
            value={zscore ? fmtNum(zscore.zscore, 2) : "—"}
            sub={zscore ? `Trailing mean ${fmtNum(zscore.trailing_mean, 2)}` : "Needs 3+ sessions"}
          />
          <StatCard label="Sessions recorded" value={String(sessionCount)} sub={symbol} />
        </div>

        <div className="flex flex-col gap-4">
          <ChartCard
            title={`${metricLabel} History`}
            action={
              <Select value={metric} onValueChange={(v) => setMetric(v as HistoryMetric)}>
                <SelectTrigger className="h-7 w-40 border-none bg-background/10 text-xs text-background">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {METRIC_OPTIONS.map((m) => (
                    <SelectItem key={m.value} value={m.value}>
                      {m.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            }
          >
            {loading ? (
              <Skeleton className="h-60 w-full" />
            ) : sessionCount < MIN_POINTS ? (
              <div className="flex h-60 flex-col items-center justify-center gap-1 text-center text-sm text-muted-foreground">
                <p>Not enough history yet to chart a trend.</p>
                <p className="text-xs">
                  {sessionCount} session{sessionCount === 1 ? "" : "s"} recorded — history accumulates day over
                  day via the daily job or the Refresh button.
                </p>
              </div>
            ) : (
              <TrendChart data={metricPoints} yLabel={metricLabel} color={COLOR_CALL} />
            )}
          </ChartCard>

          <ChartCard title="Price History (Close)">
            {loading ? (
              <Skeleton className="h-60 w-full" />
            ) : pricePoints.length < MIN_POINTS ? (
              <div className="flex h-60 flex-col items-center justify-center gap-1 text-center text-sm text-muted-foreground">
                <p>Not enough price history yet to chart a trend.</p>
                <p className="text-xs">{pricePoints.length} day(s) recorded so far.</p>
              </div>
            ) : (
              <TrendChart data={pricePoints} yLabel="Close" color="#1baf7a" />
            )}
          </ChartCard>
        </div>
      </main>
    </>
  );
}
