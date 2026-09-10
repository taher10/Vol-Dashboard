"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";

import { BacktestEquityChart, type EquitySeries } from "@/components/charts/backtest-equity-chart";
import { PayoffChart } from "@/components/charts/payoff-chart";
import { ChartCard } from "@/components/chart-card";
import { InfoHint } from "@/components/info-hint";
import { SiteHeader } from "@/components/site-header";
import { TradeCard } from "@/components/trade-card";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Slider } from "@/components/ui/slider";
import { Switch } from "@/components/ui/switch";
import {
  api,
  ApiError,
  BACKTEST_STRUCTURES,
  structureHasWidth,
  structureIsCalendar,
  type BacktestRunResponse,
  type BacktestStructure,
  type EquityPoint,
  type ExpiryOption,
} from "@/lib/api";
import { fmtDate, fmtNum } from "@/lib/format";
import { COLOR_CALL, COLOR_PUT, RICHNESS_BG, RICHNESS_TEXT } from "@/lib/theme";

const WIDTH_OPTIONS = [1, 2, 3, 4, 5];

function useBacktestRun(
  symbol: string,
  entryDate: string,
  expiration: string,
  structure: BacktestStructure,
  targetDelta: number,
  widthStrikes: number,
  backExpiration: string,
  enabled: boolean
) {
  const [data, setData] = useState<BacktestRunResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const needsBackExpiration = structureIsCalendar(structure);

  useEffect(() => {
    if (!enabled || !entryDate || !expiration) return;
    if (needsBackExpiration && !backExpiration) return;
    let cancelled = false;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- standard fetch-reset pattern, guarded by `cancelled`
    setLoading(true);
    setError(null);
    api
      .runBacktest(symbol, {
        entryDate,
        expiration,
        structure,
        targetDelta,
        widthStrikes,
        backExpiration: needsBackExpiration ? backExpiration : undefined,
      })
      .then((res) => {
        if (cancelled) return;
        setData(res);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err instanceof ApiError ? err.message : "Failed to run backtest.");
        setData(null);
      })
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [symbol, entryDate, expiration, structure, targetDelta, widthStrikes, backExpiration, needsBackExpiration, enabled]);

  return { data, loading, error };
}

/** Expirations later than the currently-chosen front expiration -- what a
 * calendar's back-month picker offers (its whole point is a LATER
 * expiration than the front leg). Empty if `frontExpiration` isn't (yet) a
 * real entry in `expirations`. */
function laterExpirations(expirations: ExpiryOption[], frontExpiration: string): ExpiryOption[] {
  const front = expirations.find((e) => e.expiration === frontExpiration);
  if (!front) return [];
  return expirations.filter((e) => e.dte > front.dte);
}

function StructureControls({
  label,
  structure,
  onStructureChange,
  delta,
  onDeltaChange,
  width,
  onWidthChange,
  backExpiration,
  onBackExpirationChange,
  backExpirationOptions,
}: {
  label: string;
  structure: BacktestStructure;
  onStructureChange: (s: BacktestStructure) => void;
  delta: number;
  onDeltaChange: (d: number) => void;
  width: number;
  onWidthChange: (w: number) => void;
  backExpiration: string;
  onBackExpirationChange: (e: string) => void;
  backExpirationOptions: ExpiryOption[];
}) {
  const isCalendar = structureIsCalendar(structure);

  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
      <div className="flex flex-col gap-1.5">
        <Label className="text-xs text-muted-foreground">{label}</Label>
        <Select value={structure} onValueChange={(v) => onStructureChange(v as BacktestStructure)}>
          <SelectTrigger className="w-full">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {BACKTEST_STRUCTURES.map((s) => (
              <SelectItem key={s.value} value={s.value}>
                {s.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <div className="flex flex-col gap-1.5">
        <Label className="flex items-center justify-between text-xs text-muted-foreground">
          <span>Target delta</span>
          <span className="font-mono tabular-nums text-foreground">{delta.toFixed(2)}</span>
        </Label>
        <Slider
          value={[delta]}
          onValueChange={(v) => onDeltaChange(v[0])}
          min={0.1}
          max={0.45}
          step={0.05}
          className="mt-2.5"
        />
      </div>

      {isCalendar ? (
        <div className="flex flex-col gap-1.5">
          <Label className="text-xs text-muted-foreground">Back-month expiration</Label>
          <Select
            value={backExpiration}
            onValueChange={onBackExpirationChange}
            disabled={backExpirationOptions.length === 0}
          >
            <SelectTrigger className="w-full">
              <SelectValue placeholder={backExpirationOptions.length === 0 ? "No later expiration" : "Select"} />
            </SelectTrigger>
            <SelectContent>
              {backExpirationOptions.map((e) => (
                <SelectItem key={e.expiration} value={e.expiration}>
                  {fmtDate(e.expiration)} (DTE {e.dte})
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      ) : (
        <div className="flex flex-col gap-1.5">
          <Label className="text-xs text-muted-foreground">Width (strikes)</Label>
          <Select
            value={String(width)}
            onValueChange={(v) => onWidthChange(Number(v))}
            disabled={!structureHasWidth(structure)}
          >
            <SelectTrigger className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {WIDTH_OPTIONS.map((w) => (
                <SelectItem key={w} value={String(w)}>
                  {w}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      )}
    </div>
  );
}

export default function BacktestPage() {
  const params = useParams<{ symbol: string }>();
  const symbol = params.symbol.toUpperCase();

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

  const [structureA, setStructureA] = useState<BacktestStructure>("bull_call");
  const [deltaA, setDeltaA] = useState(0.3);
  const [widthA, setWidthA] = useState(2);
  const [backExpirationA, setBackExpirationA] = useState("");

  const [compareMode, setCompareMode] = useState(false);
  const [structureB, setStructureB] = useState<BacktestStructure>("bull_put");
  const [deltaB, setDeltaB] = useState(0.3);
  const [widthB, setWidthB] = useState(2);
  const [backExpirationB, setBackExpirationB] = useState("");

  const [pinned, setPinned] = useState<{ seriesKey: "a" | "b"; label: string; color: string; point: EquityPoint } | null>(
    null
  );

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

  // Calendar's back-month expiration -- default to whichever later
  // expiration is closest to 30 DTE, same "closest to 30" convention the
  // front expiration itself defaults to above. Re-picks whenever the
  // available later options change (new entry date/expiration) and the
  // current selection is no longer one of them.
  useEffect(() => {
    const later = laterExpirations(expirations, expiration);
    if (later.length === 0) {
      setBackExpirationA("");
      return;
    }
    if (!later.some((e) => e.expiration === backExpirationA)) {
      const closest = later.reduce((best, e) => (Math.abs(e.dte - 30) < Math.abs(best.dte - 30) ? e : best));
      // eslint-disable-next-line react-hooks/set-state-in-effect -- re-derives a default only when the current selection has fallen out of range
      setBackExpirationA(closest.expiration);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- backExpirationA deliberately excluded, see comment above
  }, [expirations, expiration]);

  useEffect(() => {
    const later = laterExpirations(expirations, expiration);
    if (later.length === 0) {
      setBackExpirationB("");
      return;
    }
    if (!later.some((e) => e.expiration === backExpirationB)) {
      const closest = later.reduce((best, e) => (Math.abs(e.dte - 30) < Math.abs(best.dte - 30) ? e : best));
      // eslint-disable-next-line react-hooks/set-state-in-effect -- re-derives a default only when the current selection has fallen out of range
      setBackExpirationB(closest.expiration);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- backExpirationB deliberately excluded, see comment above
  }, [expirations, expiration]);

  const runA = useBacktestRun(symbol, entryDate, expiration, structureA, deltaA, widthA, backExpirationA, true);
  const runB = useBacktestRun(symbol, entryDate, expiration, structureB, deltaB, widthB, backExpirationB, compareMode);

  // Drop a stale pin from a structure/run that's no longer showing (e.g.
  // compare mode just got switched off, or a parameter changed and the
  // pinned day's series no longer exists).
  useEffect(() => {
    if (pinned?.seriesKey === "b" && !compareMode) setPinned(null);
  }, [compareMode, pinned]);

  const resultA = runA.data?.result ?? null;
  const resultB = compareMode ? (runB.data?.result ?? null) : null;
  const loading = datesLoading || expirationsLoading || runA.loading || (compareMode && runB.loading);

  const series: EquitySeries[] = [
    ...(resultA ? [{ key: "a" as const, label: resultA.entry_candidate.structure, color: COLOR_CALL, points: resultA.equity_curve }] : []),
    ...(resultB ? [{ key: "b" as const, label: resultB.entry_candidate.structure, color: COLOR_PUT, points: resultB.equity_curve }] : []),
  ];

  // In compare mode, size both payoff charts off the same X/Y domain so a
  // shallower-looking line can't visually understate real risk just because
  // its own chart auto-scaled tighter -- side-by-side risk comparison is the
  // whole point of compare mode, so mismatched scales would actively mislead.
  const sharedPayoffDomains = (() => {
    if (!compareMode || !resultA || !resultB) return null;
    const points = [...resultA.entry_candidate.payoff, ...resultB.entry_candidate.payoff];
    if (points.length === 0) return null;
    const xs = points.map((p) => p.underlying);
    const ys = points.map((p) => p.pnl);
    return {
      xDomain: [Math.min(...xs), Math.max(...xs)] as [number, number],
      yDomain: [Math.min(...ys), Math.max(...ys)] as [number, number],
    };
  })();

  function handlePointClick(seriesKey: "a" | "b", point: EquityPoint) {
    const s = series.find((s) => s.key === seriesKey);
    if (!s) return;
    setPinned({ seriesKey, label: s.label, color: s.color, point });
  }

  return (
    <>
      <SiteHeader title={`Backtest — ${symbol}`} />
      <main className="flex-1 overflow-y-auto p-4 md:p-6">
        <p className="mb-4 text-xs text-muted-foreground">
          Simulates opening this trade on a real recorded historical date and marks it to market using real
          recorded prices since then -- not a multi-year statistical backtest. History is still short (a handful of
          recorded days per symbol) and grows every day the daily pipeline runs; compare mode runs a second
          structure against the exact same real entry snapshot, which the current history already supports well.
        </p>

        {(datesError || runA.error || (compareMode && runB.error)) && (
          <div className="mb-4 rounded-md border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive">
            {datesError ?? runA.error ?? runB.error}
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
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
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

            <div className="border-t border-border pt-4">
              <StructureControls
                label={compareMode ? "Strategy A" : "Strategy"}
                structure={structureA}
                onStructureChange={setStructureA}
                delta={deltaA}
                onDeltaChange={setDeltaA}
                width={widthA}
                onWidthChange={setWidthA}
                backExpiration={backExpirationA}
                onBackExpirationChange={setBackExpirationA}
                backExpirationOptions={laterExpirations(expirations, expiration)}
              />
            </div>

            <div className="flex items-center gap-2 border-t border-border pt-4">
              <Switch checked={compareMode} onCheckedChange={setCompareMode} />
              <Label className="text-sm">Compare against a second strategy</Label>
            </div>

            {compareMode && (
              <StructureControls
                label="Strategy B"
                structure={structureB}
                onStructureChange={setStructureB}
                delta={deltaB}
                onDeltaChange={setDeltaB}
                width={widthB}
                onWidthChange={setWidthB}
                backExpiration={backExpirationB}
                onBackExpirationChange={setBackExpirationB}
                backExpirationOptions={laterExpirations(expirations, expiration)}
              />
            )}
          </div>
        )}

        {dates.length > 0 && (
          <>
            <div className={compareMode ? "grid grid-cols-1 gap-4 lg:grid-cols-2" : "grid grid-cols-1 gap-4"}>
              <ChartCard title={compareMode ? "Entry Trade — A" : "Entry Trade"}>
                {loading && !runA.data ? (
                  <Skeleton className="h-80 w-full" />
                ) : runA.data?.error ? (
                  <p className="py-6 text-center text-sm text-muted-foreground">{runA.data.error}</p>
                ) : resultA ? (
                  <div className="flex flex-col gap-4">
                    <TradeCard candidate={resultA.entry_candidate} />
                    <PayoffChart
                      payoff={resultA.entry_candidate.payoff}
                      breakevens={resultA.entry_candidate.breakevens}
                      spot={resultA.equity_curve[0]?.underlying_price}
                      height={200}
                      xDomain={sharedPayoffDomains?.xDomain}
                      yDomain={sharedPayoffDomains?.yDomain}
                      emptyMessage={
                        structureIsCalendar(structureA)
                          ? "Calendar payoff isn't modeled -- see the walk-forward P&L below, computed from real recorded option prices only."
                          : undefined
                      }
                    />
                  </div>
                ) : (
                  <p className="py-6 text-center text-sm text-muted-foreground">
                    Pick an entry date and expiration to simulate a trade.
                  </p>
                )}
              </ChartCard>

              {compareMode && (
                <ChartCard title="Entry Trade — B">
                  {runB.loading && !runB.data ? (
                    <Skeleton className="h-80 w-full" />
                  ) : runB.data?.error ? (
                    <p className="py-6 text-center text-sm text-muted-foreground">{runB.data.error}</p>
                  ) : resultB ? (
                    <div className="flex flex-col gap-4">
                      <TradeCard candidate={resultB.entry_candidate} />
                      <PayoffChart
                        payoff={resultB.entry_candidate.payoff}
                        breakevens={resultB.entry_candidate.breakevens}
                        spot={resultB.equity_curve[0]?.underlying_price}
                        height={200}
                        xDomain={sharedPayoffDomains?.xDomain}
                        yDomain={sharedPayoffDomains?.yDomain}
                        emptyMessage={
                          structureIsCalendar(structureB)
                            ? "Calendar payoff isn't modeled -- see the walk-forward P&L below, computed from real recorded option prices only."
                            : undefined
                        }
                      />
                    </div>
                  ) : (
                    <p className="py-6 text-center text-sm text-muted-foreground">No trade to plot.</p>
                  )}
                </ChartCard>
              )}
            </div>

            <div className="mt-4">
              <ChartCard
                title="Equity Curve"
                hint="Real recorded mark-to-market P&L per share since entry, using actual recorded option prices on every day the pipeline captured a snapshot -- not a modeled/theoretical curve. Click a point to pin its detail below; click a legend entry to hide/show that line."
              >
                {loading && series.length === 0 ? (
                  <Skeleton className="h-72 w-full" />
                ) : series.some((s) => s.points.length > 1) ? (
                  <>
                    <BacktestEquityChart series={series} onPointClick={handlePointClick} />
                    <p className="mt-1.5 text-center text-[11px] text-muted-foreground">
                      Click a point for that day&apos;s detail{series.length > 1 ? " · click a legend entry to hide it" : ""}
                    </p>
                  </>
                ) : (
                  <p className="py-6 text-center text-sm text-muted-foreground">
                    Not much walk-forward history yet -- check back as more daily snapshots accumulate.
                  </p>
                )}

                {pinned && (
                  <div
                    className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 rounded-md border border-border bg-muted px-3 py-2 text-xs"
                    style={{ borderColor: pinned.color }}
                  >
                    <span className="flex items-center gap-1.5 font-medium">
                      <span className="size-2 rounded-full" style={{ backgroundColor: pinned.color }} />
                      {pinned.label}
                    </span>
                    <span className="text-muted-foreground">
                      {fmtDate(pinned.point.date)} · {pinned.point.dte_remaining} DTE left
                    </span>
                    <span className="text-muted-foreground">
                      Underlying{" "}
                      <span className="font-mono text-foreground">{fmtNum(pinned.point.underlying_price, 2)}</span>
                    </span>
                    <span className="text-muted-foreground">
                      P&L/share <span className="font-mono text-foreground">{fmtNum(pinned.point.pnl_per_share, 2)}</span>
                    </span>
                    <button
                      type="button"
                      className="ml-auto text-muted-foreground underline underline-offset-2 hover:text-foreground"
                      onClick={() => setPinned(null)}
                    >
                      Clear
                    </button>
                  </div>
                )}
              </ChartCard>
            </div>
          </>
        )}

        {resultA && (
          <div className="mt-4 flex flex-wrap items-center gap-3 rounded-lg border border-border bg-accent/60 px-4 py-3 text-sm text-accent-foreground">
            <span
              className="inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium"
              style={
                resultA.status === "open"
                  ? { backgroundColor: "rgba(42, 120, 214, 0.14)", color: COLOR_CALL }
                  : { backgroundColor: RICHNESS_BG.neutral, color: RICHNESS_TEXT.neutral }
              }
            >
              {resultA.status === "open" ? "Open" : "Closed"}
            </span>
            <span>{resultA.summary}</span>
          </div>
        )}
        {compareMode && resultB && (
          <div className="mt-2 flex flex-wrap items-center gap-3 rounded-lg border border-border bg-accent/60 px-4 py-3 text-sm text-accent-foreground">
            <span
              className="inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium"
              style={
                resultB.status === "open"
                  ? { backgroundColor: "rgba(227, 73, 72, 0.14)", color: COLOR_PUT }
                  : { backgroundColor: RICHNESS_BG.neutral, color: RICHNESS_TEXT.neutral }
              }
            >
              {resultB.status === "open" ? "Open" : "Closed"}
            </span>
            <span>{resultB.summary}</span>
          </div>
        )}
      </main>
    </>
  );
}
