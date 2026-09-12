"use client";

import { useEffect, useMemo, useState } from "react";
import { ArrowDown, ArrowUp } from "lucide-react";

import { ChartCard } from "@/components/chart-card";
import { SiteHeader } from "@/components/site-header";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { CoverageHeatmap } from "@/components/charts/coverage-heatmap";
import { api, ApiError, type DataTrustResponse, type DataTrustRow } from "@/lib/api";
import { fmtInt, fmtNum } from "@/lib/format";
import { cn } from "@/lib/utils";

type SortKey = keyof Pick<
  DataTrustRow,
  "symbol" | "age_trading_days" | "n_observations" | "days_covered_in_window" | "largest_gap_trading_days"
>;

const COLUMNS: { key: SortKey; label: string; align?: "right" }[] = [
  { key: "symbol", label: "Symbol" },
  { key: "age_trading_days", label: "Age (trading days)", align: "right" },
  { key: "n_observations", label: "Observations", align: "right" },
  { key: "days_covered_in_window", label: "Days in window", align: "right" },
  { key: "largest_gap_trading_days", label: "Largest gap", align: "right" },
];

/** Thresholds are a stated rule of thumb for colouring only -- the raw
 * observation count sits next to them so the reader never has to trust the
 * label over the number. */
function observationTone(n: number): string {
  if (n < 10) return "text-[#8f2323]";
  if (n < 30) return "text-[#8a6d1f]";
  return "text-[#0b5c0b]";
}

export default function DataTrustPage() {
  const [data, setData] = useState<DataTrustResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [sortKey, setSortKey] = useState<SortKey>("age_trading_days");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");

  useEffect(() => {
    let cancelled = false;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- standard fetch-reset pattern, guarded by `cancelled`
    setLoading(true);
    setError(null);
    api
      .dataTrust()
      .then((res) => !cancelled && setData(res))
      .catch((err) => !cancelled && setError(err instanceof ApiError ? err.message : "Failed to load data trust report."))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, []);

  const sorted = useMemo(() => {
    const copy = [...(data?.rows ?? [])];
    copy.sort((a, b) => {
      const av = a[sortKey];
      const bv = b[sortKey];
      if (typeof av === "string" || typeof bv === "string") {
        return sortDir === "asc" ? String(av).localeCompare(String(bv)) : String(bv).localeCompare(String(av));
      }
      // A symbol with no data at all sorts to the bottom rather than being
      // treated as age 0 -- "never reported" is the worst case, not the best.
      const an = av === null ? Number.NEGATIVE_INFINITY : (av as number);
      const bn = bv === null ? Number.NEGATIVE_INFINITY : (bv as number);
      return sortDir === "asc" ? an - bn : bn - an;
    });
    return copy;
  }, [data, sortKey, sortDir]);

  function toggleSort(key: SortKey) {
    if (key === sortKey) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      setSortDir("desc");
    }
  }

  const staleDays = data?.trading_days_since_complete_run ?? null;
  const verdictTone =
    staleDays === null || staleDays > 5
      ? "border-destructive/30 bg-destructive/10 text-destructive"
      : staleDays > 1
        ? "border-[#8a6d1f]/30 bg-[#8a6d1f]/10 text-[#8a6d1f]"
        : "border-[#0b5c0b]/30 bg-[#0b5c0b]/10 text-[#0b5c0b]";

  return (
    <>
      <SiteHeader title="Data Trust" />
      <main className="flex-1 overflow-y-auto p-4 md:p-6">
        <p className="mb-4 text-xs text-muted-foreground">
          Every other page renders whatever the latest stored snapshot holds, with no indication of when that snapshot
          is from or how many observations sit behind it -- an IV Rank computed from 3 stored days looks identical to
          one computed from 300. This page shows the coverage the rest of the dashboard is quietly standing on.
          Expected collection days are weekdays, plus any off-schedule day that actually produced data — a manually
          triggered weekend run counts. There&apos;s no market-holiday calendar in this project, so a real holiday shows
          as a missing day rather than being silently papered over.
        </p>

        {error && (
          <div className="mb-4 rounded-md border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive">
            {error}
          </div>
        )}

        {loading || !data ? (
          <Skeleton className="h-96 w-full" />
        ) : (
          <>
            <div className={cn("mb-4 rounded-md border px-4 py-3 text-sm", verdictTone)}>
              {data.latest_complete_run ? (
                <>
                  <span className="font-semibold">
                    Last complete pipeline run: {data.latest_complete_run}
                    {staleDays !== null && staleDays > 0 && ` — ${staleDays} trading day${staleDays === 1 ? "" : "s"} ago`}
                  </span>
                  <span className="ml-1 opacity-90">
                    (a run counts as complete when at least {data.complete_run_threshold} of {data.symbol_count}{" "}
                    symbols recorded metrics). Anything older than a day or two means the numbers on every other page
                    are describing a market that has since moved.
                  </span>
                </>
              ) : (
                <span className="font-semibold">
                  No complete pipeline run in the last {data.calendar.length} trading days.
                </span>
              )}
            </div>

            <div className="mb-4">
              <ChartCard
                title="Pipeline Coverage"
                hint="One cell per symbol per expected trading day. Filled means metrics were recorded that day. A vertical band of empty cells means the whole pipeline was down -- a single sparse row means one symbol is lagging."
              >
                <CoverageHeatmap
                  calendar={data.calendar}
                  rows={data.rows}
                  reportingPerDay={data.symbols_reporting_per_day}
                  symbolCount={data.symbol_count}
                />
              </ChartCard>
            </div>

            <ChartCard title="Per-Symbol Coverage" bodyClassName="p-0">
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
                    <TableHead className="text-right">Percentile resolution</TableHead>
                    <TableHead>Last snapshot</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {sorted.map((row) => (
                    <TableRow key={row.symbol}>
                      <TableCell className="sticky left-0 z-10 bg-card font-medium">
                        <span className="inline-flex items-center gap-2">
                          <span className="size-2 rounded-full" style={{ backgroundColor: row.color }} />
                          {row.symbol}
                        </span>
                      </TableCell>
                      <TableCell className="text-right font-mono tabular-nums">
                        {row.age_trading_days === null ? "never" : fmtInt(row.age_trading_days)}
                      </TableCell>
                      <TableCell
                        className={cn("text-right font-mono tabular-nums", observationTone(row.n_observations))}
                      >
                        {fmtInt(row.n_observations)}
                      </TableCell>
                      <TableCell className="text-right font-mono tabular-nums">
                        {fmtInt(row.days_covered_in_window)} / {data.calendar.length}
                      </TableCell>
                      <TableCell className="text-right font-mono tabular-nums">
                        {fmtInt(row.largest_gap_trading_days)}
                      </TableCell>
                      <TableCell className="text-right font-mono tabular-nums text-muted-foreground">
                        {row.percentile_resolution_pts === null ? "—" : `±${fmtNum(row.percentile_resolution_pts, 1)} pts`}
                      </TableCell>
                      <TableCell className="text-xs text-muted-foreground">{row.last_snapshot_date ?? "—"}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </ChartCard>

            <p className="mt-3 text-[11px] text-muted-foreground">
              Percentile resolution is 100/n: with n stored observations an IV Rank or percentile can only land on n
              distinct values, so it resolves no finer than this. It&apos;s a fact about the sample, not a judgement
              about the signal -- but a rank quoted to the nearest point off a handful of observations is claiming
              precision the data doesn&apos;t have.
            </p>
          </>
        )}
      </main>
    </>
  );
}
