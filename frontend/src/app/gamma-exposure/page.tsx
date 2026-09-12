"use client";

import { useEffect, useMemo, useState } from "react";
import { ArrowDown, ArrowUp } from "lucide-react";

import { ChartCard } from "@/components/chart-card";
import { SiteHeader } from "@/components/site-header";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { VolBarChart, type VolBarRow } from "@/components/charts/vol-bar-chart";
import { api, ApiError, type GammaExposureRow } from "@/lib/api";
import { fmtInt, fmtSigned, fmtUsd } from "@/lib/format";
import { cn } from "@/lib/utils";

type SortKey = keyof Pick<
  GammaExposureRow,
  "symbol" | "underlying_price" | "total_gamma_exposure" | "peak_strike" | "peak_strike_distance_pct"
>;

const COLUMNS: { key: SortKey; label: string; align?: "right" }[] = [
  { key: "symbol", label: "Symbol" },
  { key: "underlying_price", label: "Spot", align: "right" },
  { key: "total_gamma_exposure", label: "Gamma Exposure ($/1% move)", align: "right" },
  { key: "peak_strike", label: "Peak Strike", align: "right" },
  { key: "peak_strike_distance_pct", label: "Dist. from Spot", align: "right" },
];

export default function GammaExposurePage() {
  const [rows, setRows] = useState<GammaExposureRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [sortKey, setSortKey] = useState<SortKey>("total_gamma_exposure");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");

  useEffect(() => {
    let cancelled = false;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- standard fetch-reset pattern, guarded by `cancelled`
    setLoading(true);
    setError(null);
    api
      .gammaExposure()
      .then((res) => !cancelled && setRows(res.rows))
      .catch((err) => !cancelled && setError(err instanceof ApiError ? err.message : "Failed to load gamma exposure data."))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, []);

  const barRows: VolBarRow[] = useMemo(
    () => rows.map((r) => ({ symbol: r.symbol, color: r.color, value: r.total_gamma_exposure })),
    [rows]
  );

  const sorted = useMemo(() => {
    const copy = [...rows];
    copy.sort((a, b) => {
      const av = a[sortKey];
      const bv = b[sortKey];
      if (typeof av === "string" || typeof bv === "string") {
        return sortDir === "asc" ? String(av).localeCompare(String(bv)) : String(bv).localeCompare(String(av));
      }
      return sortDir === "asc" ? (av as number) - (bv as number) : (bv as number) - (av as number);
    });
    return copy;
  }, [rows, sortKey, sortDir]);

  function toggleSort(key: SortKey) {
    if (key === sortKey) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      setSortDir("desc");
    }
  }

  return (
    <>
      <SiteHeader title="Gamma Exposure" />
      <main className="flex-1 overflow-y-auto p-4 md:p-6">
        <p className="mb-4 text-xs text-muted-foreground">
          Total dollar-gamma (gamma × open interest, both calls and puts, at the expiration nearest 30 DTE) per
          symbol -- a magnitude, ranked by how much gamma-driven hedging flow is concentrated in that name right now,
          not a directional signal. This is deliberately unsigned: open interest alone doesn&apos;t say which side of
          a contract dealers are actually on, so a netted call-minus-put &quot;dealer positioning&quot; number would
          be more precise-looking than the data actually supports. Peak strike is where that gamma is most
          concentrated -- a candidate pinning level, not a price target.
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
            <div className="mb-4">
              <ChartCard
                title="Gamma Exposure Leaderboard"
                hint="Total gamma x OI (calls + puts) at the nearest-30-DTE expiration, scaled to a dollar-per-1%-move figure. Ranked by magnitude -- higher means more gamma-driven hedging flow concentrated in that name."
              >
                <VolBarChart
                  rows={barRows}
                  valueLabel="Gamma Exposure"
                  formatValue={(v) => `$${fmtUsd(v, 0)}`}
                  emptyMessage="No symbols have a live expiration with usable gamma/OI data yet."
                />
              </ChartCard>
            </div>

            <ChartCard title="Gamma Exposure Details" bodyClassName="p-0">
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
                    <TableHead>Expiration</TableHead>
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
                      <TableCell className="text-right font-mono tabular-nums">{fmtUsd(row.underlying_price, 2)}</TableCell>
                      <TableCell className="text-right font-mono tabular-nums">
                        ${fmtUsd(row.total_gamma_exposure, 0)}
                      </TableCell>
                      <TableCell className="text-right font-mono tabular-nums">{fmtInt(row.peak_strike)}</TableCell>
                      <TableCell className="text-right font-mono tabular-nums">
                        {fmtSigned(row.peak_strike_distance_pct, 1)}%
                      </TableCell>
                      <TableCell className="text-xs text-muted-foreground">
                        {row.expiration} ({row.dte}d)
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </ChartCard>
          </>
        )}
      </main>
    </>
  );
}
