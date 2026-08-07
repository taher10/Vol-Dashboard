"use client";

import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { ArrowDown, ArrowUp } from "lucide-react";

import { ChartCard } from "@/components/chart-card";
import { SiteHeader } from "@/components/site-header";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { api, ApiError, type ScannerRow } from "@/lib/api";
import { fmtInt, fmtNum, fmtSigned } from "@/lib/format";
import { RICHNESS_BG, RICHNESS_TEXT, richnessKey } from "@/lib/theme";
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

  return (
    <>
      <SiteHeader title="Vol Scanner" />
      <main className="flex-1 overflow-y-auto p-4 md:p-6">
        <p className="mb-4 text-xs text-muted-foreground">
          One representative (~30 DTE) expiry per symbol, sortable -- click a column header to sort, click a row to
          open its Expiry Drilldown. Symbols with little recorded history show blanks where a signal needs more days
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
                  <TableHead>Skew Bias</TableHead>
                  <TableHead>Richness</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {sorted.map((row) => {
                  const key = richnessKey(row.richness_label);
                  return (
                    <TableRow
                      key={row.symbol}
                      className="cursor-pointer"
                      onClick={() => router.push(`/expiry/${row.symbol}`)}
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
                      <TableCell className="text-xs text-muted-foreground">{row.skew_bias ?? "—"}</TableCell>
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
        )}
      </main>
    </>
  );
}
