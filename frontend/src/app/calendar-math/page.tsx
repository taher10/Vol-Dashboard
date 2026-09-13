"use client";

import { Fragment, useEffect, useMemo, useState } from "react";
import { ArrowDown, ArrowUp, ChevronDown, ChevronRight } from "lucide-react";

import { ChartCard } from "@/components/chart-card";
import { SiteHeader } from "@/components/site-header";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { VolBarChart, type VolBarRow } from "@/components/charts/vol-bar-chart";
import { api, ApiError, type CalendarEdgeRow } from "@/lib/api";
import { fmtDate, fmtDateTime, fmtNum, fmtSigned, fmtUsd } from "@/lib/format";
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

const FRONT_DTE_CHOICES = [0, 7, 14, 21];
const BACK_DTE_CHOICES = [30, 45, 60, 90];
const DELTA_CHOICES = [0.15, 0.25, 0.35, 0.45];

function Control({
  label,
  value,
  choices,
  format,
  onChange,
}: {
  label: string;
  value: number;
  choices: number[];
  format: (v: number) => string;
  onChange: (v: number) => void;
}) {
  return (
    <div className="flex items-center gap-1.5">
      <span className="text-[11px] uppercase tracking-wide text-muted-foreground">{label}</span>
      <div className="inline-flex overflow-hidden rounded-md border border-border">
        {choices.map((c) => (
          <button
            key={c}
            type="button"
            onClick={() => onChange(c)}
            className={cn(
              "px-2 py-1 font-mono text-xs tabular-nums transition-colors",
              c === value ? "bg-foreground text-background" : "bg-card hover:bg-accent"
            )}
          >
            {format(c)}
          </button>
        ))}
      </div>
    </div>
  );
}

/** Per-row expansion: the legs and the decomposition the ranking is built
 * from. Deliberately not a payoff chart -- a calendar's payoff at front
 * expiration depends on the back leg's remaining time value, which needs an
 * options-pricing model this app doesn't have, so build_calendar_call()
 * leaves payoff/max_profit/breakevens empty on purpose. Plotting a curve
 * here would mean inventing the very number the rest of the page is careful
 * not to claim. These are all real recorded values instead. */
function StructureDetail({ row }: { row: CalendarEdgeRow }) {
  const ve = row.candidate.variance_edge;
  // Stacked, left-pinned and width-capped rather than a 2-column grid: this
  // sits in a cell spanning every column of a table wide enough to scroll
  // horizontally, so a second grid column lands off-screen to the right, and a
  // full-width block would only be readable after scrolling sideways. sticky
  // keeps it against the left edge when the table is scrolled.
  return (
    <div className="sticky left-0 flex w-fit min-w-[22rem] flex-col gap-4 border-l-2 border-border bg-muted/30 px-4 py-3">
      <div>
        <div className="mb-1.5 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">Legs</div>
        <table className="w-auto text-xs">
          <thead className="text-muted-foreground">
            <tr>
              <th className="text-left font-normal">Action</th>
              <th className="pl-6 text-right font-normal">Strike</th>
              <th className="pl-6 text-right font-normal">IV</th>
              <th className="pl-6 text-right font-normal">Vega</th>
              <th className="pl-6 text-right font-normal">Mid</th>
              <th className="pl-6 text-right font-normal">Expiry</th>
            </tr>
          </thead>
          <tbody className="font-mono tabular-nums">
            {row.candidate.legs.map((leg, i) => (
              <tr key={i}>
                <td className={cn("text-left font-sans", leg.action === "sell" ? "text-neg" : "text-pos")}>
                  {leg.action} {leg.optionType.toLowerCase()}
                </td>
                <td className="pl-6 text-right">{fmtNum(leg.strike, 0)}</td>
                <td className="pl-6 text-right">{fmtNum(leg.implied_volatility, 1)}</td>
                <td className="pl-6 text-right">{fmtNum(leg.vega, 2)}</td>
                <td className="pl-6 text-right">{fmtUsd(leg.mid)}</td>
                <td className="pl-6 text-right text-muted-foreground">{fmtDate(leg.expiration)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="mt-1.5 text-[11px] leading-relaxed text-muted-foreground">
          Legs are matched by delta, not strike — so the two strikes usually differ, which is exactly why est. max
          loss can exceed the net debit.
        </p>
      </div>

      <div>
        <div className="mb-1.5 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
          How the edge is derived
        </div>
        {ve ? (
          <>
            <table className="w-auto text-xs">
              <tbody className="font-mono tabular-nums">
                <tr>
                  <td className="text-left font-sans text-muted-foreground">Post-event baseline IV</td>
                  <td className="pl-6 text-right">{fmtNum(ve.iv_ex, 2)}</td>
                </tr>
                <tr>
                  <td className="text-left font-sans text-muted-foreground">Front leg expected crush</td>
                  <td className="pl-6 text-right">{fmtSigned(ve.front_crush, 2)}</td>
                </tr>
                <tr>
                  <td className="text-left font-sans text-muted-foreground">Back leg expected crush</td>
                  <td className="pl-6 text-right">{fmtSigned(ve.back_crush, 2)}</td>
                </tr>
                <tr>
                  <td className="text-left font-sans text-muted-foreground">Front leg P&amp;L (crush × vega)</td>
                  <td className={cn("pl-6 text-right", ve.front_vega_pnl >= 0 ? "text-pos" : "text-neg")}>
                    {fmtSigned(ve.front_vega_pnl, 0)}
                  </td>
                </tr>
                <tr>
                  <td className="text-left font-sans text-muted-foreground">Back leg P&amp;L (crush × vega)</td>
                  <td className={cn("pl-6 text-right", ve.back_vega_pnl >= 0 ? "text-pos" : "text-neg")}>
                    {fmtSigned(ve.back_vega_pnl, 0)}
                  </td>
                </tr>
                <tr className="border-t border-border">
                  <td className="text-left font-sans font-medium">Net modeled edge</td>
                  <td
                    className={cn(
                      "pl-6 text-right font-medium",
                      ve.net_vega_pnl >= 0 ? "text-pos" : "text-neg"
                    )}
                  >
                    {fmtSigned(ve.net_vega_pnl, 0)}
                  </td>
                </tr>
              </tbody>
            </table>
            <p className="mt-1.5 text-[11px] leading-relaxed text-muted-foreground">
              A heuristic, not a simulation: it assumes the same absolute event variance lands in both expirations.
              No payoff curve is shown because a calendar&apos;s value at front expiration depends on the back
              leg&apos;s remaining time value — there is no honest number for that without a pricing model.
            </p>
          </>
        ) : (
          <p className="text-xs text-muted-foreground">No measurable edge decomposition for this symbol.</p>
        )}
      </div>
    </div>
  );
}

export default function CalendarMathPage() {
  const [rows, setRows] = useState<CalendarEdgeRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [sortKey, setSortKey] = useState<SortKey>("edge_per_capital_pct");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");
  const [expanded, setExpanded] = useState<string | null>(null);

  const [frontDte, setFrontDte] = useState(7);
  const [backDte, setBackDte] = useState(30);
  const [targetDelta, setTargetDelta] = useState(0.25);

  // Same-day estimate, every symbol in one call -- unlike a real backtest,
  // needs no walk-forward history, so a symbol added yesterday shows up here
  // immediately.
  useEffect(() => {
    let cancelled = false;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- standard fetch-reset pattern, guarded by `cancelled`
    setLoading(true);
    setError(null);
    api
      .calendarEdge(frontDte, backDte, targetDelta)
      .then((res) => !cancelled && setRows(res.rows))
      .catch(
        (err) =>
          !cancelled &&
          // Surface what actually failed. A bare "failed to load" can't be
          // told apart from "no symbol has an edge right now", which is a
          // legitimate empty result rather than a fault.
          setError(
            err instanceof ApiError
              ? `Failed to load calendar edge estimates: ${err.message}`
              : "Failed to load calendar edge estimates. Is the API running on port 8000?"
          )
      )
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [frontDte, backDte, targetDelta]);

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

  const asOf = rows.length > 0 ? rows[0].as_of : null;

  return (
    <>
      <SiteHeader title="Calendar Math" />
      <main className="flex-1 overflow-y-auto p-4 md:p-6">
        <p className="mb-3 text-xs text-muted-foreground">
          A sell-front/buy-back-month calendar call spread, ranked by a MODELED estimate -- a forward-variance
          decomposition using each leg&apos;s real recorded IV and broker-supplied vega, not a guarantee or a
          backtested win rate. Net debit is the real capital required. Est. max loss is a genuine worst-case UPPER
          BOUND -- real strike/premium algebra (a big enough move can lose more than the debit when front/back strikes
          differ, since legs are delta-matched rather than same-strike), conservatively ignoring the back leg&apos;s
          remaining time value, so the true worst case is typically a bit lower than shown, never higher. Max profit
          isn&apos;t shown -- unlike max loss, it&apos;s reached at an interior stock price rather than a boundary, so
          there&apos;s no honest number for it without a real options-pricing model. Click any row to see both legs
          and how the edge was derived. For a real (if short) track record of an actual trade, run it on the Backtest
          page.
        </p>

        <div className="mb-4 flex flex-wrap items-center gap-x-5 gap-y-2">
          <Control
            label="Front DTE"
            value={frontDte}
            choices={FRONT_DTE_CHOICES}
            format={(v) => String(v)}
            onChange={setFrontDte}
          />
          <Control
            label="Back DTE"
            value={backDte}
            choices={BACK_DTE_CHOICES}
            format={(v) => String(v)}
            onChange={setBackDte}
          />
          <Control
            label="Delta"
            value={targetDelta}
            choices={DELTA_CHOICES}
            format={(v) => v.toFixed(2)}
            onChange={setTargetDelta}
          />
          {asOf && (
            <span className="text-[11px] text-muted-foreground">
              Snapshot: {fmtDateTime(asOf)} · {rows.length} symbols with a measurable edge
            </span>
          )}
        </div>

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
                emptyMessage="No symbol currently shows a measurable calendar edge at these settings."
              />
            </ChartCard>

            <ChartCard title="Calendar Details" bodyClassName="p-0">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-8" />
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
                    <Fragment key={row.symbol}>
                      <TableRow
                        className="cursor-pointer"
                        onClick={() => setExpanded((s) => (s === row.symbol ? null : row.symbol))}
                      >
                        <TableCell className="text-muted-foreground">
                          {expanded === row.symbol ? (
                            <ChevronDown className="size-3.5" />
                          ) : (
                            <ChevronRight className="size-3.5" />
                          )}
                        </TableCell>
                        <TableCell className="sticky left-0 z-10 bg-card font-medium">
                          <span className="inline-flex items-center gap-2">
                            <span className="size-2 rounded-full" style={{ backgroundColor: row.color }} />
                            {row.symbol}
                          </span>
                        </TableCell>
                        <TableCell className="text-right font-mono tabular-nums">
                          {fmtUsd(row.net_debit_credit)}
                        </TableCell>
                        <TableCell className="text-right font-mono tabular-nums text-neg">
                          {fmtUsd(row.est_max_loss)}
                        </TableCell>
                        <TableCell
                          className={cn(
                            "text-right font-mono tabular-nums",
                            row.net_vega_pnl >= 0 ? "text-pos" : "text-neg"
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
                      {expanded === row.symbol && (
                        <TableRow className="hover:bg-transparent">
                          <TableCell colSpan={COLUMNS.length + 3} className="p-0">
                            <StructureDetail row={row} />
                          </TableCell>
                        </TableRow>
                      )}
                    </Fragment>
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
