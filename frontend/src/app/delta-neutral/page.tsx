"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import { ChartCard } from "@/components/chart-card";
import { SiteHeader } from "@/components/site-header";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { api, ApiError, type DeltaNeutralRow } from "@/lib/api";
import { fmtDate, fmtNum, fmtPct, fmtSigned, fmtUsd } from "@/lib/format";
import { cn } from "@/lib/utils";

function StraddleTable({ rows, emptyMessage }: { rows: DeltaNeutralRow[]; emptyMessage: string }) {
  const router = useRouter();

  if (rows.length === 0) {
    return <p className="py-6 text-center text-sm text-muted-foreground">{emptyMessage}</p>;
  }

  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Symbol</TableHead>
          <TableHead>Structure</TableHead>
          <TableHead>Expiration</TableHead>
          <TableHead className="text-right">Richness z</TableHead>
          <TableHead className="text-right">Call / Put Strike</TableHead>
          <TableHead className="text-right">Net Debit/Credit</TableHead>
          <TableHead className="text-right">Max Profit</TableHead>
          <TableHead className="text-right">Max Loss</TableHead>
          <TableHead className="text-right">Approx. POP</TableHead>
          <TableHead className="text-right">Breakevens</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((row) => {
          const c = row.candidate;
          const call = c.legs.find((l) => l.optionType === "CALL");
          const put = c.legs.find((l) => l.optionType === "PUT");
          return (
            <TableRow key={row.symbol} className="cursor-pointer" onClick={() => router.push(`/strategy/${row.symbol}`)}>
              <TableCell className="font-medium">
                <span className="inline-flex items-center gap-2">
                  <span className="size-2 rounded-full" style={{ backgroundColor: row.color }} />
                  {row.symbol}
                </span>
              </TableCell>
              <TableCell className="text-xs text-muted-foreground">{c.structure}</TableCell>
              <TableCell className="text-xs text-muted-foreground">
                {fmtDate(row.expiration)} (DTE {row.dte})
              </TableCell>
              <TableCell className="text-right font-mono tabular-nums">{fmtSigned(row.richness_z, 2)}</TableCell>
              <TableCell className="text-right font-mono tabular-nums">
                {fmtNum(call?.strike, 1)} / {fmtNum(put?.strike, 1)}
              </TableCell>
              <TableCell className="text-right font-mono tabular-nums">{fmtUsd(Math.abs(c.net_debit_credit))}</TableCell>
              <TableCell className="text-right font-mono tabular-nums text-[#0b5c0b]">
                {c.max_profit != null ? fmtUsd(c.max_profit) : "Uncapped"}
              </TableCell>
              <TableCell className="text-right font-mono tabular-nums text-[#8f2323]">
                {c.max_loss != null ? fmtUsd(c.max_loss) : "Uncapped"}
              </TableCell>
              <TableCell className="text-right font-mono tabular-nums">
                {c.approx_pop != null ? fmtPct(c.approx_pop * 100, 0) : "—"}
              </TableCell>
              <TableCell className="text-right font-mono tabular-nums">
                {c.breakevens.map((b) => fmtNum(b, 1)).join(" / ")}
              </TableCell>
            </TableRow>
          );
        })}
      </TableBody>
    </Table>
  );
}

export default function DeltaNeutralPage() {
  const [rows, setRows] = useState<DeltaNeutralRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Same-day screen, every symbol in one call -- no walk-forward history
  // needed (real backtest verification for this structure is a later step).
  useEffect(() => {
    let cancelled = false;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- standard fetch-reset pattern, guarded by `cancelled`
    setLoading(true);
    setError(null);
    api
      .deltaNeutral()
      .then((res) => !cancelled && setRows(res.rows))
      .catch((err) => !cancelled && setError(err instanceof ApiError ? err.message : "Failed to load delta-neutral screen."))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, []);

  const sellRows = rows
    .filter((r) => r.action === "sell")
    .sort((a, b) => b.richness_z - a.richness_z);
  const buyRows = rows
    .filter((r) => r.action === "buy")
    .sort((a, b) => a.richness_z - b.richness_z);

  return (
    <>
      <SiteHeader title="Delta Neutral" />
      <main className="flex-1 overflow-y-auto p-4 md:p-6">
        <p className="mb-4 text-xs text-muted-foreground">
          A same-day screen for a delta-neutral-at-entry straddle/strangle (call + put at/near the same strike, ~30
          DTE by default) per symbol, split by the same richness_z signal used everywhere else in this app: rich
          (IV priced above its own history) suggests selling to collect the premium; cheap suggests buying for a
          big-move bet. Every number here is real -- actual strikes and quoted premiums from the latest chain, not a
          backtest. The side that's genuinely open-ended (a short position's loss above the call strike, a long
          position's profit either way) shows &quot;Uncapped&quot; rather than a fabricated number. Click a row to
          open that symbol&apos;s Strategy Builder. Real walk-forward backtesting for this structure isn&apos;t built
          yet.
        </p>

        {error && (
          <div className="mb-4 rounded-md border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive">
            {error}
          </div>
        )}

        {loading ? (
          <Skeleton className="h-96 w-full" />
        ) : (
          <div className="flex flex-col gap-4">
            <ChartCard
              title="Richest — sell candidates"
              hint="IV priced above this symbol's own historical range at this expiry -- selling the straddle/strangle collects that premium. Profits if the stock stays between the breakevens through expiration, or if IV crushes. Max loss is genuinely uncapped above the call strike."
              bodyClassName="p-0"
            >
              <StraddleTable rows={sellRows} emptyMessage="No symbol currently screens as rich enough to rank here." />
            </ChartCard>

            <ChartCard
              title="Cheapest — buy candidates"
              hint="IV priced below this symbol's own historical range at this expiry -- buying the straddle/strangle is a bet on a bigger move than the market is currently pricing. Max loss is capped at the premium paid; max profit is genuinely uncapped."
              bodyClassName="p-0"
            >
              <StraddleTable rows={buyRows} emptyMessage="No symbol currently screens as cheap enough to rank here." />
            </ChartCard>

            <p
              className={cn(
                "text-center text-xs text-muted-foreground",
                rows.length > 0 ? "hidden" : ""
              )}
            >
              No symbols have enough history for a richness signal yet.
            </p>
          </div>
        )}
      </main>
    </>
  );
}
