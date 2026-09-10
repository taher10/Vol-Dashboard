"use client";

import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { ArrowDown, ArrowUp } from "lucide-react";

import { ChartCard } from "@/components/chart-card";
import { InfoHint } from "@/components/info-hint";
import { SiteHeader } from "@/components/site-header";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { VolBarChart, type VolBarRow } from "@/components/charts/vol-bar-chart";
import { VolScatterChart, type VolScatterPoint } from "@/components/charts/vol-scatter-chart";
import { StrikeProfileChart, type StrikeProfileMetric } from "@/components/charts/strike-profile-chart";
import { api, ApiError, type CalendarEdgeRow, type ExpiryOption, type ScannerRow, type StrikeProfileRow } from "@/lib/api";
import { fmtDate, fmtInt, fmtNum, fmtSigned } from "@/lib/format";
import { RICHNESS_BG, RICHNESS_HINT, RICHNESS_TEXT, SKEW_BIAS_HINT, richnessKey } from "@/lib/theme";
import { cn } from "@/lib/utils";

type SortKey = keyof Pick<
  ScannerRow,
  "symbol" | "underlying_price" | "atm_iv" | "iv_rank" | "richness_z" | "skew" | "curvature" | "days_of_history"
>;

const COLUMNS: { key: SortKey; label: string; align?: "right" }[] = [
  { key: "symbol", label: "Symbol" },
  { key: "underlying_price", label: "Price", align: "right" },
  { key: "atm_iv", label: "ATM IV (~30d)", align: "right" },
  { key: "iv_rank", label: "IV Rank", align: "right" },
  { key: "richness_z", label: "Richness z", align: "right" },
  { key: "skew", label: "Skew", align: "right" },
  { key: "curvature", label: "Curvature", align: "right" },
  { key: "days_of_history", label: "History", align: "right" },
];

export default function ScannerPage() {
  const router = useRouter();
  const [data, setData] = useState<ScannerRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [sortKey, setSortKey] = useState<SortKey>("richness_z");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");

  const [pcrExpirations, setPcrExpirations] = useState<ExpiryOption[]>([]);
  // Empty string (not null) so the Select stays controlled from the first
  // render -- same reason as Backtest's entry-date Select.
  const [pcrExpiration, setPcrExpiration] = useState("");
  const [pcrRows, setPcrRows] = useState<VolBarRow[]>([]);
  const [pcrLoading, setPcrLoading] = useState(true);
  const [pcrError, setPcrError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- standard fetch-reset pattern, guarded by `cancelled`
    setLoading(true);
    setError(null);
    api
      .scanner()
      .then((res) => !cancelled && setData(res.rows))
      .catch((err) => !cancelled && setError(err instanceof ApiError ? err.message : "Failed to load scanner data."))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, []);

  // Available expirations for the PCR chart -- and the backend's own
  // nearest-to-30-DTE default, so the chart isn't empty before the user
  // picks anything.
  useEffect(() => {
    let cancelled = false;
    api
      .scannerPcr()
      .then((res) => {
        if (cancelled) return;
        setPcrExpirations(res.available_expirations);
        setPcrExpiration(res.expiration ?? "");
      })
      .catch(() => !cancelled && setPcrError("Failed to load available expirations."));
    return () => {
      cancelled = true;
    };
  }, []);

  // Refetched whenever the selected expiration changes -- only symbols that
  // literally list this exact calendar expiration come back, matching what
  // the user asked to see (not every symbol's own nearest-30-DTE pick).
  useEffect(() => {
    if (!pcrExpiration) return;
    let cancelled = false;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- standard fetch-reset pattern, guarded by `cancelled`
    setPcrLoading(true);
    setPcrError(null);
    api
      .scannerPcr(pcrExpiration)
      .then((res) => {
        if (cancelled) return;
        setPcrRows(res.rows.map((r) => ({ symbol: r.symbol, color: r.color, value: r.pcr })));
      })
      .catch(() => !cancelled && setPcrError("Failed to load put/call ratio for this expiration."))
      .finally(() => !cancelled && setPcrLoading(false));
    return () => {
      cancelled = true;
    };
  }, [pcrExpiration]);

  const [calendarEdgeRows, setCalendarEdgeRows] = useState<CalendarEdgeRow[]>([]);
  const [calendarEdgeLoading, setCalendarEdgeLoading] = useState(true);
  const [calendarEdgeError, setCalendarEdgeError] = useState<string | null>(null);

  // Same-day estimate, every symbol in one call -- unlike a real backtest,
  // needs no walk-forward history, so a symbol added yesterday shows up here
  // immediately.
  useEffect(() => {
    let cancelled = false;
    api
      .calendarEdge()
      .then((res) => !cancelled && setCalendarEdgeRows(res.rows))
      .catch(() => !cancelled && setCalendarEdgeError("Failed to load calendar edge estimates."))
      .finally(() => !cancelled && setCalendarEdgeLoading(false));
    return () => {
      cancelled = true;
    };
  }, []);

  const [strikeSymbol, setStrikeSymbol] = useState("");
  const [strikeExpirations, setStrikeExpirations] = useState<ExpiryOption[]>([]);
  // Empty string (not null) so the Select stays controlled from the first
  // render -- same reason as the other Selects on this page.
  const [strikeExpiration, setStrikeExpiration] = useState("");
  const [strikeMetric, setStrikeMetric] = useState<StrikeProfileMetric>("iv");
  const [strikeRows, setStrikeRows] = useState<StrikeProfileRow[]>([]);
  const [strikeUnderlyingPrice, setStrikeUnderlyingPrice] = useState<number | null>(null);
  const [strikeLoading, setStrikeLoading] = useState(false);
  const [strikeError, setStrikeError] = useState<string | null>(null);

  // Default the strike-profile symbol to the first loaded scanner row, once.
  useEffect(() => {
    if (!strikeSymbol && data.length > 0) {
      setStrikeSymbol(data[0].symbol);
    }
  }, [data, strikeSymbol]);

  function handleStrikeSymbolChange(symbol: string) {
    setStrikeExpiration(""); // this symbol's expirations may not include the old one
    setStrikeSymbol(symbol);
  }

  // Refetched on symbol or expiration change -- resolves the backend's own
  // nearest-30-DTE default when expiration is still "".
  useEffect(() => {
    if (!strikeSymbol) return;
    let cancelled = false;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- standard fetch-reset pattern, guarded by `cancelled`
    setStrikeLoading(true);
    setStrikeError(null);
    api
      .scannerStrikeProfile(strikeSymbol, strikeExpiration || undefined)
      .then((res) => {
        if (cancelled) return;
        setStrikeExpirations(res.available_expirations);
        setStrikeExpiration(res.expiration ?? "");
        setStrikeUnderlyingPrice(res.underlying_price);
        setStrikeRows(res.strikes);
      })
      .catch(() => !cancelled && setStrikeError("Failed to load strike profile."))
      .finally(() => !cancelled && setStrikeLoading(false));
    return () => {
      cancelled = true;
    };
  }, [strikeSymbol, strikeExpiration]);

  // Default sort is |richness_z| descending ("most notable first"), matching
  // build_takeaway()'s own .abs().idxmax() selection elsewhere in the app --
  // the *displayed* value stays signed (fmtSigned), only the sort order uses
  // magnitude. Nulls always sort last regardless of direction, since a thin-
  // history symbol not having a signal yet shouldn't interleave randomly
  // with symbols that do.
  const sorted = useMemo(() => {
    const rows = [...data];
    rows.sort((a, b) => {
      let av: number | string | null = a[sortKey];
      let bv: number | string | null = b[sortKey];
      if (sortKey === "richness_z") {
        av = av === null ? null : Math.abs(av as number);
        bv = bv === null ? null : Math.abs(bv as number);
      }
      if (av === null && bv === null) return 0;
      if (av === null) return 1;
      if (bv === null) return -1;
      if (typeof av === "string" || typeof bv === "string") {
        return sortDir === "asc" ? String(av).localeCompare(String(bv)) : String(bv).localeCompare(String(av));
      }
      return sortDir === "asc" ? (av as number) - (bv as number) : (bv as number) - (av as number);
    });
    return rows;
  }, [data, sortKey, sortDir]);

  function toggleSort(key: SortKey) {
    if (key === sortKey) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      setSortDir("desc");
    }
  }

  const ivVsRvPoints: VolScatterPoint[] = useMemo(
    () =>
      data
        .filter((r) => r.atm_iv !== null && r.realized_vol !== null)
        .map((r) => ({ symbol: r.symbol, color: r.color, x: r.realized_vol as number, y: r.atm_iv as number })),
    [data]
  );

  const ivRankRows: VolBarRow[] = useMemo(
    () => data.filter((r) => r.iv_rank !== null).map((r) => ({ symbol: r.symbol, color: r.color, value: r.iv_rank as number })),
    [data]
  );

  const vrpRows: VolBarRow[] = useMemo(
    () =>
      data
        .filter((r) => r.atm_iv !== null && r.realized_vol !== null)
        .map((r) => ({ symbol: r.symbol, color: r.color, value: (r.atm_iv as number) - (r.realized_vol as number) })),
    [data]
  );

  const calendarEdgeBarRows: VolBarRow[] = useMemo(
    () => calendarEdgeRows.map((r) => ({ symbol: r.symbol, color: r.color, value: r.net_vega_pnl })),
    [calendarEdgeRows]
  );

  return (
    <>
      <SiteHeader title="Vol Scanner" />
      <main className="flex-1 overflow-y-auto p-4 md:p-6">
        <p className="mb-4 text-xs text-muted-foreground">
          One representative (~30 DTE) expiry per symbol, sortable -- click a column header to sort, click a row to
          open its Strategy Builder. Symbols with little recorded history show blanks where a signal needs more days
          to compute (see the History column), not zeros.
        </p>

        {error && (
          <div className="mb-4 rounded-md border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive">
            {error}
          </div>
        )}

        {loading ? (
          <Skeleton className="h-96 w-full" />
        ) : (
          <>
            <div className="mb-4 grid grid-cols-1 gap-4 lg:grid-cols-2">
              <ChartCard
                title="IV vs Realized Vol"
                hint="Each dot is one symbol's representative expiry. Same comparison as Richness (ATM IV vs. this symbol's own realized vol) -- above the line, options are pricing more movement than the stock has actually been making."
              >
                <VolScatterChart
                  points={ivVsRvPoints}
                  xLabel="Realized Vol (30d)"
                  yLabel="ATM IV"
                  aboveLineMeans="IV priced above realized vol"
                  belowLineMeans="IV priced below realized vol"
                  emptyMessage="No symbols have both IV and realized vol yet."
                />
              </ChartCard>
              <ChartCard
                title="Put/Call Ratio"
                hint="Total put open interest divided by total call open interest, summed across every strike at the expiration selected below -- a positioning read, not a pricing one. Above 1: more puts than calls held open (skews defensive/bearish). Below 1: more calls (skews speculative/bullish). Only symbols that actually list this expiration, with open interest on both sides, appear."
              >
                <div className="mb-3 flex items-center justify-end gap-2">
                  <Label className="text-xs text-muted-foreground">Expiration</Label>
                  <Select
                    value={pcrExpiration}
                    onValueChange={setPcrExpiration}
                    disabled={pcrExpirations.length === 0}
                  >
                    <SelectTrigger size="sm" className="w-44">
                      <SelectValue placeholder="Select expiration" />
                    </SelectTrigger>
                    <SelectContent>
                      {pcrExpirations.map((e) => (
                        <SelectItem key={e.expiration} value={e.expiration}>
                          {fmtDate(e.expiration)} (DTE {e.dte})
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                {pcrError && <p className="mb-2 text-xs text-destructive">{pcrError}</p>}
                {pcrLoading ? (
                  <Skeleton className="h-[300px] w-full" />
                ) : (
                  <VolBarChart
                    rows={pcrRows}
                    valueLabel="PCR"
                    height={300}
                    referenceValue={1}
                    formatValue={(v) => fmtNum(v, 2)}
                    emptyMessage="No symbols list this expiration with open interest on both sides."
                  />
                )}
              </ChartCard>
            </div>
            <ChartCard
              title="Strike Profile"
              hint="Per-strike detail for one symbol+expiration -- IV, delta, and gamma only exist at this level, not summarized per expiry the way the charts above are. IV is one theoretical value per strike in this data source (identical for calls and puts at the same strike), so the smile is one curve; gamma is likewise ~equal call vs put by put-call parity, so its two-sided cut here is gamma weighted by each side's own open interest instead."
              className="mb-4"
            >
              <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
                <div className="flex flex-wrap items-center gap-4">
                  <div className="flex items-center gap-2">
                    <Label className="text-xs text-muted-foreground">Symbol</Label>
                    <Select value={strikeSymbol} onValueChange={handleStrikeSymbolChange} disabled={data.length === 0}>
                      <SelectTrigger size="sm" className="w-28">
                        <SelectValue placeholder="Symbol" />
                      </SelectTrigger>
                      <SelectContent>
                        {data.map((r) => (
                          <SelectItem key={r.symbol} value={r.symbol}>
                            {r.symbol}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                  <div className="flex items-center gap-2">
                    <Label className="text-xs text-muted-foreground">Expiration</Label>
                    <Select
                      value={strikeExpiration}
                      onValueChange={setStrikeExpiration}
                      disabled={strikeExpirations.length === 0}
                    >
                      <SelectTrigger size="sm" className="w-44">
                        <SelectValue placeholder="Select expiration" />
                      </SelectTrigger>
                      <SelectContent>
                        {strikeExpirations.map((e) => (
                          <SelectItem key={e.expiration} value={e.expiration}>
                            {fmtDate(e.expiration)} (DTE {e.dte})
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                </div>
                <ToggleGroup
                  type="single"
                  value={strikeMetric}
                  onValueChange={(v) => v && setStrikeMetric(v as StrikeProfileMetric)}
                >
                  <ToggleGroupItem value="iv">IV Smile</ToggleGroupItem>
                  <ToggleGroupItem value="delta">Delta</ToggleGroupItem>
                  <ToggleGroupItem value="gammaOi">Gamma × OI</ToggleGroupItem>
                </ToggleGroup>
              </div>
              {strikeError && <p className="mb-2 text-xs text-destructive">{strikeError}</p>}
              {strikeLoading ? (
                <Skeleton className="h-[320px] w-full" />
              ) : (
                <StrikeProfileChart
                  rows={strikeRows}
                  metric={strikeMetric}
                  underlyingPrice={strikeUnderlyingPrice}
                  emptyMessage="No strike data for this symbol/expiration."
                />
              )}
            </ChartCard>
            <div className="mb-4 grid grid-cols-1 gap-4 lg:grid-cols-2">
              <ChartCard
                title="IV Rank Leaderboard"
                hint="Where each symbol's current ATM IV sits within its own trailing 1-year range (0 = year low, 100 = year high). Longer memory than Richness z, which only looks at trailing IV history at this DTE bucket."
              >
                <VolBarChart rows={ivRankRows} valueLabel="IV Rank" emptyMessage="No symbols have enough history for an IV Rank yet." />
              </ChartCard>
              <ChartCard
                title="VRP Leaderboard"
                hint="ATM IV minus realized vol, ranked -- the same comparison as the IV vs Realized Vol chart above, but sorted so the richest premium-selling candidates are easy to scan top to bottom."
              >
                <VolBarChart rows={vrpRows} valueLabel="VRP" emptyMessage="No symbols have both IV and realized vol yet." />
              </ChartCard>
            </div>
            <ChartCard
              title="Calendar Edge Leaderboard"
              hint="Estimated dollar edge for a sell-front/buy-back-month 25Δ calendar call spread (~7 DTE front, ~30 DTE back), ranked. This is a MODELED estimate -- a forward-variance decomposition using each leg's real recorded IV and broker-supplied vega, not a guarantee or a backtested win rate. Needs no walk-forward history, so a symbol added yesterday still shows up here; for a real (if short) track record of an actual trade, run it on the Backtest page."
              className="mb-4"
            >
              {calendarEdgeError && <p className="mb-2 text-xs text-destructive">{calendarEdgeError}</p>}
              {calendarEdgeLoading ? (
                <Skeleton className="h-64 w-full" />
              ) : (
                <VolBarChart
                  rows={calendarEdgeBarRows}
                  valueLabel="Modeled edge"
                  referenceValue={0}
                  formatValue={(v) => `$${fmtNum(v, 0)}`}
                  emptyMessage="No symbol currently shows a measurable calendar edge."
                />
              )}
            </ChartCard>
            <ChartCard title="Vol Scanner" bodyClassName="p-0">
            <Table>
              <TableHeader>
                <TableRow>
                  {COLUMNS.map((col, i) => (
                    <TableHead
                      key={col.key}
                      className={cn(
                        "cursor-pointer select-none",
                        col.align === "right" && "text-right",
                        i === 0 && "sticky left-0 z-10 bg-card"
                      )}
                      onClick={() => toggleSort(col.key)}
                    >
                      <span className={cn("inline-flex items-center gap-1", col.align === "right" && "justify-end")}>
                        {col.label}
                        {sortKey === col.key &&
                          (sortDir === "desc" ? <ArrowDown className="size-3" /> : <ArrowUp className="size-3" />)}
                      </span>
                    </TableHead>
                  ))}
                  <TableHead>
                    Skew Bias <InfoHint text={SKEW_BIAS_HINT} />
                  </TableHead>
                  <TableHead>
                    Richness <InfoHint text={RICHNESS_HINT} />
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {sorted.map((row) => {
                  const key = richnessKey(row.richness_label);
                  return (
                    <TableRow
                      key={row.symbol}
                      className="cursor-pointer"
                      onClick={() => router.push(`/strategy/${row.symbol}`)}
                    >
                      <TableCell className="sticky left-0 z-10 bg-card font-medium">
                        <span className="inline-flex items-center gap-2">
                          <span className="size-2 rounded-full" style={{ backgroundColor: row.color }} />
                          {row.symbol}
                        </span>
                      </TableCell>
                      <TableCell className="text-right font-mono tabular-nums">
                        {row.underlying_price != null ? `$${fmtNum(row.underlying_price, 2)}` : "—"}
                      </TableCell>
                      <TableCell className="text-right font-mono tabular-nums">{fmtNum(row.atm_iv, 2)}</TableCell>
                      <TableCell className="text-right font-mono tabular-nums">{fmtNum(row.iv_rank, 0)}</TableCell>
                      <TableCell className="text-right font-mono tabular-nums">{fmtSigned(row.richness_z, 2)}</TableCell>
                      <TableCell className="text-right font-mono tabular-nums">{fmtNum(row.skew, 2)}</TableCell>
                      <TableCell className="text-right font-mono tabular-nums">{fmtNum(row.curvature, 2)}</TableCell>
                      <TableCell className="text-right font-mono tabular-nums">{fmtInt(row.days_of_history)}</TableCell>
                      <TableCell className="text-xs text-muted-foreground">
                        {row.has_wing_data ? row.skew_bias : "—"}
                      </TableCell>
                      <TableCell>
                        {/* Gated on richness_z, not richness_label -- the backend always returns SOME
                            label (e.g. "Neutral" is the documented fallback for "no signal yet", not a
                            real reading), so checking the label alone would show a fabricated-looking
                            pill for thin-history symbols with no real richness signal computed. */}
                        {row.richness_z !== null && row.richness_label ? (
                          <span
                            className="inline-flex rounded-full px-2 py-0.5 text-xs font-medium"
                            style={{ backgroundColor: RICHNESS_BG[key], color: RICHNESS_TEXT[key] }}
                          >
                            {row.richness_label}
                          </span>
                        ) : (
                          <span className="text-xs text-muted-foreground">—</span>
                        )}
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
            </ChartCard>
          </>
        )}
      </main>
    </>
  );
}
