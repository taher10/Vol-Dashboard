"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";

import { ChartCard } from "@/components/chart-card";
import { InfoHint } from "@/components/info-hint";
import { SiteHeader } from "@/components/site-header";
import { TradeCard } from "@/components/trade-card";
import { TrendChart, type TrendPoint } from "@/components/charts/trend-chart";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { api, ApiError, type BacktestRunResponse, type ExpiryOption } from "@/lib/api";
import { fmtDate } from "@/lib/format";
import { COLOR_LINE_DEFAULT, RICHNESS_BG, RICHNESS_TEXT } from "@/lib/theme";

type Direction = "bullish" | "bearish";
type Risk = "conservative" | "moderate" | "aggressive";

export default function BacktestPage() {
  const params = useParams<{ symbol: string }>();
  const symbol = params.symbol.toUpperCase();

  const [direction, setDirection] = useState<Direction>("bullish");
  const [risk, setRisk] = useState<Risk>("moderate");

  const [dates, setDates] = useState<string[]>([]);
  // Empty string (not null) so the Select stays controlled from the first
  // render -- switching a Radix Select's `value` from undefined to a real
  // string once data loads triggers a dev-mode "uncontrolled to controlled"
  // warning; starting controlled with "" avoids that entirely.
  const [entryDate, setEntryDate] = useState("");
  const [datesLoading, setDatesLoading] = useState(true);
  const [datesError, setDatesError] = useState<string | null>(null);

  const [expirations, setExpirations] = useState<ExpiryOption[]>([]);
  const [expiration, setExpiration] = useState("");
  const [expirationsLoading, setExpirationsLoading] = useState(false);

  const [data, setData] = useState<BacktestRunResponse | null>(null);
  const [runLoading, setRunLoading] = useState(false);
  const [runError, setRunError] = useState<string | null>(null);

  // Available entry dates for this symbol -- default to the EARLIEST one so
  // there's the most real walk-forward history to show right away.
  useEffect(() => {
    let cancelled = false;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- standard fetch-reset pattern, guarded by `cancelled`
    setDatesLoading(true);
    setDatesError(null);
    api
      .backtestDates(symbol)
      .then((res) => {
        if (cancelled) return;
        setDates(res.dates);
        setEntryDate(res.dates[0] ?? "");
      })
      .catch((err) => {
        if (cancelled) return;
        setDatesError(err instanceof ApiError ? err.message : "Failed to load available dates.");
        setDates([]);
        setEntryDate("");
      })
      .finally(() => !cancelled && setDatesLoading(false));
    return () => {
      cancelled = true;
    };
  }, [symbol]);

  // Expirations actually listed on the chosen entry date -- default to
  // whichever is closest to 30 DTE, refetched whenever entryDate changes.
  useEffect(() => {
    if (!entryDate) return;
    let cancelled = false;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- standard fetch-reset pattern, guarded by `cancelled`
    setExpirationsLoading(true);
    setExpiration("");
    api
      .backtestExpirations(symbol, entryDate)
      .then((res) => {
        if (cancelled) return;
        setExpirations(res.expirations);
        if (res.expirations.length > 0) {
          const closest = res.expirations.reduce((best, e) => (Math.abs(e.dte - 30) < Math.abs(best.dte - 30) ? e : best));
          setExpiration(closest.expiration);
        }
      })
      .catch(() => {
        if (cancelled) return;
        setExpirations([]);
      })
      .finally(() => !cancelled && setExpirationsLoading(false));
    return () => {
      cancelled = true;
    };
  }, [symbol, entryDate]);

  // Run the simulation whenever every parameter is in place.
  useEffect(() => {
    if (!entryDate || !expiration) return;
    let cancelled = false;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- standard fetch-reset pattern, guarded by `cancelled`
    setRunLoading(true);
    setRunError(null);
    api
      .runBacktest(symbol, { entryDate, expiration, direction, risk })
      .then((res) => {
        if (cancelled) return;
        setData(res);
      })
      .catch((err) => {
        if (cancelled) return;
        setRunError(err instanceof ApiError ? err.message : "Failed to run backtest.");
        setData(null);
      })
      .finally(() => !cancelled && setRunLoading(false));
    return () => {
      cancelled = true;
    };
  }, [symbol, entryDate, expiration, direction, risk]);

  const result = data?.result ?? null;
  const loading = datesLoading || expirationsLoading || runLoading;

  const equityPoints: TrendPoint[] = result?.equity_curve.map((p) => ({ date: p.date, value: p.pnl_per_share })) ?? [];

  return (
    <>
      <SiteHeader title={`Backtest — ${symbol}`} />
      <main className="flex-1 overflow-y-auto p-4 md:p-6">
        <p className="mb-4 text-xs text-muted-foreground">
          Simulates opening this trade on a real recorded historical date and marks it to market using real
          recorded prices since then -- not a multi-year statistical backtest. History is still short and grows
          every day the daily pipeline runs.
        </p>

        {(datesError || runError) && (
          <div className="mb-4 rounded-md border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive">
            {datesError ?? runError}
          </div>
        )}

        {!datesLoading && dates.length === 0 && !datesError && (
          <div className="mb-4 rounded-md border border-border bg-muted px-4 py-3 text-sm text-muted-foreground">
            No recorded option-chain history yet for {symbol}. Check back once the daily pipeline has captured at
            least one snapshot for this symbol.
          </div>
        )}

        {(datesLoading || dates.length > 0) && (
          <div className="mb-4 flex flex-col gap-4 rounded-lg border border-border bg-card p-4">
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
              <div className="flex flex-col gap-1.5">
                <Label className="text-xs text-muted-foreground">Direction</Label>
                <ToggleGroup
                  type="single"
                  value={direction}
                  onValueChange={(v) => v && setDirection(v as Direction)}
                  className="w-full"
                >
                  <ToggleGroupItem value="bullish" className="flex-1">
                    Bullish
                  </ToggleGroupItem>
                  <ToggleGroupItem value="bearish" className="flex-1">
                    Bearish
                  </ToggleGroupItem>
                </ToggleGroup>
              </div>

              <div className="flex flex-col gap-1.5">
                <Label className="text-xs text-muted-foreground">Risk appetite</Label>
                <ToggleGroup type="single" value={risk} onValueChange={(v) => v && setRisk(v as Risk)} className="w-full">
                  <ToggleGroupItem value="conservative" className="flex-1">
                    Conservative
                  </ToggleGroupItem>
                  <ToggleGroupItem value="moderate" className="flex-1">
                    Moderate
                  </ToggleGroupItem>
                  <ToggleGroupItem value="aggressive" className="flex-1">
                    Aggressive
                  </ToggleGroupItem>
                </ToggleGroup>
              </div>

              <div className="flex flex-col gap-1.5">
                <Label className="text-xs text-muted-foreground">Entry date</Label>
                <Select value={entryDate} onValueChange={setEntryDate} disabled={dates.length === 0}>
                  <SelectTrigger className="w-full">
                    <SelectValue placeholder={datesLoading ? "Loading…" : "Select date"} />
                  </SelectTrigger>
                  <SelectContent>
                    {dates.map((d) => (
                      <SelectItem key={d} value={d}>
                        {fmtDate(d)}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>

              <div className="flex flex-col gap-1.5">
                <Label className="flex items-center gap-1 text-xs text-muted-foreground">
                  Expiration
                  <InfoHint text="Only expirations actually listed in the chain on the chosen entry date -- changes when you pick a different entry date." />
                </Label>
                <Select value={expiration} onValueChange={setExpiration} disabled={expirations.length === 0}>
                  <SelectTrigger className="w-full">
                    <SelectValue placeholder={expirationsLoading ? "Loading…" : "Select expiration"} />
                  </SelectTrigger>
                  <SelectContent>
                    {expirations.map((e) => (
                      <SelectItem key={e.expiration} value={e.expiration}>
                        {fmtDate(e.expiration)} (DTE {e.dte})
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>
          </div>
        )}

        {dates.length > 0 && (
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-[1fr_1.3fr]">
            <ChartCard title="Entry Trade">
              {loading && !data ? (
                <Skeleton className="h-64 w-full" />
              ) : data?.error ? (
                <p className="py-6 text-center text-sm text-muted-foreground">{data.error}</p>
              ) : result ? (
                <TradeCard candidate={result.entry_candidate} />
              ) : (
                <p className="py-6 text-center text-sm text-muted-foreground">
                  Pick an entry date and expiration to simulate a trade.
                </p>
              )}
            </ChartCard>

            <ChartCard
              title="Equity Curve"
              hint="Real recorded mark-to-market P&L per share since entry, using actual recorded option prices on every day the pipeline captured a snapshot -- not a modeled/theoretical curve."
            >
              {loading && !data ? (
                <Skeleton className="h-72 w-full" />
              ) : result && result.days_held > 0 ? (
                <TrendChart data={equityPoints} yLabel="P&L / share" color={COLOR_LINE_DEFAULT} />
              ) : (
                <p className="py-6 text-center text-sm text-muted-foreground">
                  {result
                    ? "Not much walk-forward history yet -- check back as more daily snapshots accumulate."
                    : "No trade to plot."}
                </p>
              )}
            </ChartCard>
          </div>
        )}

        {result && (
          <div className="mt-4 flex flex-wrap items-center gap-3 rounded-lg border border-border bg-accent/60 px-4 py-3 text-sm text-accent-foreground">
            <span
              className="inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium"
              style={
                result.status === "open"
                  ? { backgroundColor: "rgba(42, 120, 214, 0.14)", color: COLOR_LINE_DEFAULT }
                  : { backgroundColor: RICHNESS_BG.neutral, color: RICHNESS_TEXT.neutral }
              }
            >
              {result.status === "open" ? "Open" : "Closed"}
            </span>
            <span>{result.summary}</span>
          </div>
        )}
      </main>
    </>
  );
}
