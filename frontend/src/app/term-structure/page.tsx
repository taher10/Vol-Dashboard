"use client";

import { useEffect, useMemo, useState } from "react";
import { ArrowDown, ArrowUp } from "lucide-react";

import { ChartCard } from "@/components/chart-card";
import { SiteHeader } from "@/components/site-header";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { VolBarChart, type VolBarRow } from "@/components/charts/vol-bar-chart";
import { api, ApiError, type TermStructureRow } from "@/lib/api";
import { fmtNum, fmtSigned } from "@/lib/format";
import { cn } from "@/lib/utils";

type SortKey = keyof Pick<
  TermStructureRow,
  "symbol" | "near_iv" | "far_iv" | "iv_slope" | "near_skew" | "far_skew" | "skew_slope"
>;

const COLUMNS: { key: SortKey; label: string; align?: "right" }[] = [
  { key: "symbol", label: "Symbol" },
  { key: "near_iv", label: "Near IV", align: "right" },
  { key: "far_iv", label: "Far IV", align: "right" },
  { key: "iv_slope", label: "IV Slope", align: "right" },
  { key: "near_skew", label: "Near Skew", align: "right" },
  { key: "far_skew", label: "Far Skew", align: "right" },
  { key: "skew_slope", label: "Skew Slope", align: "right" },
];

export default function TermStructurePage() {
  const [rows, setRows] = useState<TermStructureRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [sortKey, setSortKey] = useState<SortKey>("iv_slope");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");

  useEffect(() => {
    let cancelled = false;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- standard fetch-reset pattern, guarded by `cancelled`
    setLoading(true);
    setError(null);
    api
      .termStructure()
      .then((res) => !cancelled && setRows(res.rows))
      .catch((err) => !cancelled && setError(err instanceof ApiError ? err.message : "Failed to load term structure data."))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, []);

  const ivSlopeBarRows: VolBarRow[] = useMemo(
    () => rows.map((r) => ({ symbol: r.symbol, color: r.color, value: r.iv_slope })),
    [rows]
  );

  const skewSlopeBarRows: VolBarRow[] = useMemo(
    () => rows.map((r) => ({ symbol: r.symbol, color: r.color, value: r.skew_slope })),
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
      <SiteHeader title="Term Structure" />
      <main className="flex-1 overflow-y-auto p-4 md:p-6">
        <p className="mb-4 text-xs text-muted-foreground">
          Near (~7 DTE) vs. far (~60 DTE) ATM IV and 25Δ skew per symbol, ranked by slope. IV slope positive means
          far-dated IV is priced above near-dated (normal/contango); negative means inverted (near-dated richer --
          often an event/earnings signal). Skew slope shows whether downside skew gets more or less pronounced
          further out. All real numbers off the same term-structure/skew curves Overview plots, not a modeled
          estimate.
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
                title="Term Structure Leaderboard"
                hint="Far ATM IV minus near ATM IV, ranked. Above zero: normal/contango (far-dated priced richer). Below zero: inverted (near-dated priced richer than far-dated) -- often signals an expected near-term event."
              >
                <VolBarChart
                  rows={ivSlopeBarRows}
                  valueLabel="IV Slope"
                  referenceValue={0}
                  formatValue={(v) => fmtSigned(v, 2)}
                  emptyMessage="No symbols have enough distinct expirations yet."
                />
              </ChartCard>
              <ChartCard
                title="Skew Term Structure Leaderboard"
                hint="Far 25Δ skew minus near 25Δ skew, ranked. Above zero: downside (put) skew gets MORE pronounced further out. Below zero: skew flattens or flips further out."
              >
                <VolBarChart
                  rows={skewSlopeBarRows}
                  valueLabel="Skew Slope"
                  referenceValue={0}
                  formatValue={(v) => fmtSigned(v, 2)}
                  emptyMessage="No symbols have enough distinct expirations yet."
                />
              </ChartCard>
            </div>

            <ChartCard title="Term Structure Details" bodyClassName="p-0">
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
                    <TableHead>DTEs</TableHead>
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
                      <TableCell className="text-right font-mono tabular-nums">{fmtNum(row.near_iv, 2)}</TableCell>
                      <TableCell className="text-right font-mono tabular-nums">{fmtNum(row.far_iv, 2)}</TableCell>
                      <TableCell
                        className={cn(
                          "text-right font-mono tabular-nums",
                          row.iv_slope >= 0 ? "text-[#0b5c0b]" : "text-[#8f2323]"
                        )}
                      >
                        {fmtSigned(row.iv_slope, 2)}
                      </TableCell>
                      <TableCell className="text-right font-mono tabular-nums">{fmtNum(row.near_skew, 2)}</TableCell>
                      <TableCell className="text-right font-mono tabular-nums">{fmtNum(row.far_skew, 2)}</TableCell>
                      <TableCell
                        className={cn(
                          "text-right font-mono tabular-nums",
                          row.skew_slope >= 0 ? "text-[#0b5c0b]" : "text-[#8f2323]"
                        )}
                      >
                        {fmtSigned(row.skew_slope, 2)}
                      </TableCell>
                      <TableCell className="text-xs text-muted-foreground">
                        {row.near_dte}d / {row.far_dte}d
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
