"use client";

import { useEffect, useMemo, useState } from "react";
import { ArrowDown, ArrowUp } from "lucide-react";

import { ChartCard } from "@/components/chart-card";
import { SiteHeader } from "@/components/site-header";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { VolBarChart, type VolBarRow } from "@/components/charts/vol-bar-chart";
import { api, ApiError, type CalendarEdgeRow } from "@/lib/api";
import { fmtDate, fmtNum, fmtSigned, fmtUsd } from "@/lib/format";
import { cn } from "@/lib/utils";

type SortKey = keyof Pick<
  CalendarEdgeRow,
  "symbol" | "net_debit_credit" | "est_max_loss" | "net_vega_pnl" | "edge_per_capital_pct"
>;

const COLUMNS: { key: SortKey; label: string; align?: "right" }[] = [
  { key: "symbol", label: "Symbol" },
  { key: "net_debit_credit", label: "Net Debit", align: "right" },
  { key: "est_max_loss", label: "Est. Max Loss", align: "right" },
  { key: "net_vega_pnl", label: "Modeled Edge", align: "right" },
  { key: "edge_per_capital_pct", label: "Edge / Capital", align: "right" },
];

export default function CalendarMathPage() {
  const [rows, setRows] = useState<CalendarEdgeRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [sortKey, setSortKey] = useState<SortKey>("edge_per_capital_pct");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");

  // Same-day estimate, every symbol in one call -- unlike a real backtest,
  // needs no walk-forward history, so a symbol added yesterday shows up here
  // immediately.
  useEffect(() => {
    let cancelled = false;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- standard fetch-reset pattern, guarded by `cancelled`
    setLoading(true);
    setError(null);
    api
      .calendarEdge()
      .then((res) => !cancelled && setRows(res.rows))
      .catch(() => !cancelled && setError("Failed to load calendar edge estimates."))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, []);

  const barRows: VolBarRow[] = useMemo(
    () => rows.map((r) => ({ symbol: r.symbol, color: r.color, value: r.net_vega_pnl })),
    [rows]
  );

  const sorted = useMemo(() => {
    const copy = [...rows];
    copy.sort((a, b) => {
      const av = a[sortKey];
      const bv = b[sortKey];
      if (av === null && bv === null) return 0;
      if (av === null) return 1;
      if (bv === null) return -1;
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
      <SiteHeader title="Calendar Math" />
      <main className="flex-1 overflow-y-auto p-4 md:p-6">
        <p className="mb-4 text-xs text-muted-foreground">
          A sell-front/buy-back-month 25Δ calendar call spread (~7 DTE front, ~30 DTE back), ranked by a MODELED
          estimate -- a forward-variance decomposition using each leg&apos;s real recorded IV and broker-supplied
          vega, not a guarantee or a backtested win rate. Net debit is the real capital required. Est. max loss is a
          genuine worst-case UPPER BOUND -- real strike/premium algebra (a big enough move can lose more than the
          debit when front/back strikes differ, since legs are delta-matched rather than same-strike), conservatively
          ignoring the back leg&apos;s remaining time value, so the true worst case is typically a bit lower than
          shown, never higher. Max profit isn&apos;t shown -- unlike max loss, it&apos;s reached at an interior stock
          price rather than a boundary, so there&apos;s no honest number for it without a real options-pricing
          model. For a real (if short) track record of an actual trade, run it on the Backtest page.
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
            <ChartCard title="Calendar Edge Leaderboard" className="mb-4">
              <VolBarChart
                rows={barRows}
                valueLabel="Modeled edge"
                referenceValue={0}
                formatValue={(v) => `$${fmtNum(v, 0)}`}
                emptyMessage="No symbol currently shows a measurable calendar edge."
              />
            </ChartCard>

            <ChartCard title="Calendar Details" bodyClassName="p-0">
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
                    <TableHead>Front Expiration</TableHead>
                    <TableHead>Back Expiration</TableHead>
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
                      <TableCell className="text-right font-mono tabular-nums">{fmtUsd(row.net_debit_credit)}</TableCell>
                      <TableCell className="text-right font-mono tabular-nums text-[#8f2323]">
                        {fmtUsd(row.est_max_loss)}
                      </TableCell>
                      <TableCell
                        className={cn(
                          "text-right font-mono tabular-nums",
                          row.net_vega_pnl >= 0 ? "text-[#0b5c0b]" : "text-[#8f2323]"
                        )}
                      >
                        {fmtSigned(row.net_vega_pnl, 0)}
                      </TableCell>
                      <TableCell className="text-right font-mono tabular-nums">
                        {row.edge_per_capital_pct != null ? `${fmtSigned(row.edge_per_capital_pct, 1)}%` : "—"}
                      </TableCell>
                      <TableCell className="text-xs text-muted-foreground">
                        {fmtDate(row.front_expiration)} (DTE {row.front_dte})
                      </TableCell>
                      <TableCell className="text-xs text-muted-foreground">
                        {fmtDate(row.back_expiration)} (DTE {row.back_dte})
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
