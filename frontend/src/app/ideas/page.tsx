"use client";

import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { ArrowDown, ArrowUp } from "lucide-react";

import { ChartCard } from "@/components/chart-card";
import { SiteHeader } from "@/components/site-header";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { api, ApiError, type TradeIdea } from "@/lib/api";
import { fmtDate, fmtNum, fmtPct } from "@/lib/format";
import { COLOR_CALL, COLOR_PUT, RICHNESS_BG, RICHNESS_TEXT, richnessKey } from "@/lib/theme";
import { cn } from "@/lib/utils";

type SortKey = keyof Pick<TradeIdea, "symbol" | "max_profit" | "max_loss" | "reward_risk" | "approx_pop" | "dte">;
type DirectionFilter = "all" | "bullish" | "bearish";
type StructureFilter = "all" | "credit" | "debit";

function SortableHead({
  label,
  active,
  dir,
  align,
  sticky,
  onClick,
}: {
  label: string;
  active: boolean;
  dir: "asc" | "desc";
  align?: "right";
  sticky?: boolean;
  onClick: () => void;
}) {
  return (
    <TableHead
      className={cn("cursor-pointer select-none", align === "right" && "text-right", sticky && "sticky left-0 z-10 bg-card")}
      onClick={onClick}
    >
      <span className={cn("inline-flex items-center gap-1", align === "right" && "justify-end")}>
        {label}
        {active && (dir === "desc" ? <ArrowDown className="size-3" /> : <ArrowUp className="size-3" />)}
      </span>
    </TableHead>
  );
}

export default function TradeIdeasPage() {
  const router = useRouter();
  const [data, setData] = useState<TradeIdea[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [directionFilter, setDirectionFilter] = useState<DirectionFilter>("all");
  const [structureFilter, setStructureFilter] = useState<StructureFilter>("all");
  const [sortKey, setSortKey] = useState<SortKey>("reward_risk");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");

  useEffect(() => {
    let cancelled = false;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- standard fetch-reset pattern, guarded by `cancelled`
    setLoading(true);
    setError(null);
    api
      .tradeIdeas()
      .then((res) => !cancelled && setData(res.ideas))
      .catch((err) => !cancelled && setError(err instanceof ApiError ? err.message : "Failed to load trade ideas."))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, []);

  // Filter first, then sort -- nulls (only possible on reward_risk, when
  // max_loss is somehow 0) always sort last regardless of direction, same
  // convention as Vol Scanner.
  const filteredSorted = useMemo(() => {
    let rows = data;
    if (directionFilter !== "all") rows = rows.filter((r) => r.direction === directionFilter);
    if (structureFilter !== "all") {
      rows = rows.filter((r) => (structureFilter === "credit" ? r.is_credit : !r.is_credit));
    }
    rows = [...rows];
    rows.sort((a, b) => {
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
    return rows;
  }, [data, directionFilter, structureFilter, sortKey, sortDir]);

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
      <SiteHeader title="Trade Ideas" />
      <main className="flex-1 overflow-y-auto p-4 md:p-6">
        <p className="mb-4 text-xs text-muted-foreground">
          One real example trade per symbol with a clear directional skew edge today -- same math Strategy Builder
          and Expiry Drilldown use, not a separate estimate. Symbols with a balanced smile (no clear edge) don't
          appear here.
        </p>

        <div className="mb-4 flex flex-wrap items-center gap-6 rounded-lg border border-border bg-card p-4">
          <div className="flex flex-col gap-1.5">
            <span className="text-xs text-muted-foreground">Direction</span>
            <ToggleGroup
              type="single"
              value={directionFilter}
              onValueChange={(v) => v && setDirectionFilter(v as DirectionFilter)}
            >
              <ToggleGroupItem value="all">All</ToggleGroupItem>
              <ToggleGroupItem value="bullish">Bullish</ToggleGroupItem>
              <ToggleGroupItem value="bearish">Bearish</ToggleGroupItem>
            </ToggleGroup>
          </div>
          <div className="flex flex-col gap-1.5">
            <span className="text-xs text-muted-foreground">Structure</span>
            <ToggleGroup
              type="single"
              value={structureFilter}
              onValueChange={(v) => v && setStructureFilter(v as StructureFilter)}
            >
              <ToggleGroupItem value="all">All</ToggleGroupItem>
              <ToggleGroupItem value="credit">Credit</ToggleGroupItem>
              <ToggleGroupItem value="debit">Debit</ToggleGroupItem>
            </ToggleGroup>
          </div>
        </div>

        {error && (
          <div className="mb-4 rounded-md border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive">
            {error}
          </div>
        )}

        {loading ? (
          <Skeleton className="h-96 w-full" />
        ) : filteredSorted.length === 0 ? (
          <div className="rounded-md border border-border bg-muted px-4 py-3 text-sm text-muted-foreground">
            {data.length === 0
              ? "No directional trade ideas today -- every tracked symbol's smile is currently balanced, or there's no saved data yet."
              : "No ideas match the current filters."}
          </div>
        ) : (
          <ChartCard title="Trade Ideas" bodyClassName="p-0">
            <Table>
              <TableHeader>
                <TableRow>
                  <SortableHead
                    label="Symbol"
                    active={sortKey === "symbol"}
                    dir={sortDir}
                    sticky
                    onClick={() => toggleSort("symbol")}
                  />
                  <TableHead>Trade</TableHead>
                  <TableHead>Legs</TableHead>
                  <TableHead className="text-right">Net Credit/Debit</TableHead>
                  <SortableHead
                    label="Max Profit"
                    active={sortKey === "max_profit"}
                    dir={sortDir}
                    align="right"
                    onClick={() => toggleSort("max_profit")}
                  />
                  <SortableHead
                    label="Max Loss"
                    active={sortKey === "max_loss"}
                    dir={sortDir}
                    align="right"
                    onClick={() => toggleSort("max_loss")}
                  />
                  <SortableHead
                    label="Reward:Risk"
                    active={sortKey === "reward_risk"}
                    dir={sortDir}
                    align="right"
                    onClick={() => toggleSort("reward_risk")}
                  />
                  <SortableHead
                    label="Approx POP"
                    active={sortKey === "approx_pop"}
                    dir={sortDir}
                    align="right"
                    onClick={() => toggleSort("approx_pop")}
                  />
                  <SortableHead
                    label="DTE / Expiration"
                    active={sortKey === "dte"}
                    dir={sortDir}
                    align="right"
                    onClick={() => toggleSort("dte")}
                  />
                  <TableHead>Skew Bias</TableHead>
                  <TableHead>Richness</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {filteredSorted.map((idea) => {
                  const key = richnessKey(idea.richness_label);
                  return (
                    <TableRow
                      key={idea.symbol}
                      className="cursor-pointer"
                      onClick={() => router.push(`/expiry/${idea.symbol}?expiration=${idea.expiration}`)}
                    >
                      <TableCell className="sticky left-0 z-10 bg-card font-medium">
                        <span className="inline-flex items-center gap-2">
                          <span className="size-2 rounded-full" style={{ backgroundColor: idea.color }} />
                          {idea.symbol}
                        </span>
                      </TableCell>
                      <TableCell>
                        {/* Neutral styling, not green/red-by-direction -- those same hex
                            values are already used for Max Profit/Max Loss two columns
                            over, where green/red mean profit/loss, not bullish/bearish.
                            Reusing them here for a different meaning in the same row read
                            as a real mixed-signal risk on review. The structure name
                            itself already says "Bull"/"Bear", so the pill doesn't need to
                            re-encode direction by color at all. */}
                        <span className="inline-flex rounded-full bg-muted px-2 py-0.5 text-xs font-medium text-foreground">
                          {idea.structure}
                        </span>
                      </TableCell>
                      <TableCell>
                        <span className="flex flex-wrap items-center gap-x-2 text-xs text-muted-foreground">
                          {idea.legs.map((leg, i) => (
                            <span key={i}>
                              {leg.action === "buy" ? "+" : "−"}
                              <span style={{ color: leg.optionType === "CALL" ? COLOR_CALL : COLOR_PUT }}>
                                {leg.optionType}
                              </span>{" "}
                              {fmtNum(leg.strike, leg.strike >= 1000 ? 0 : 1)}
                            </span>
                          ))}
                        </span>
                      </TableCell>
                      <TableCell className="text-right font-mono tabular-nums">
                        {fmtNum(Math.abs(idea.net_debit_credit), 2)} {idea.is_credit ? "Cr" : "Db"}
                      </TableCell>
                      <TableCell className="text-right font-mono tabular-nums text-[#0b5c0b]">
                        {fmtNum(idea.max_profit, 2)}
                      </TableCell>
                      <TableCell className="text-right font-mono tabular-nums text-[#8f2323]">
                        {fmtNum(idea.max_loss, 2)}
                      </TableCell>
                      <TableCell className="text-right font-mono tabular-nums">
                        {idea.reward_risk != null ? fmtNum(idea.reward_risk, 2) : "—"}
                      </TableCell>
                      <TableCell className="text-right font-mono tabular-nums">{fmtPct(idea.approx_pop * 100, 0)}</TableCell>
                      <TableCell className="text-right font-mono text-xs tabular-nums">
                        {idea.dte}d · {fmtDate(idea.expiration)}
                      </TableCell>
                      <TableCell className="text-xs text-muted-foreground">{idea.skew_bias ?? "—"}</TableCell>
                      <TableCell>
                        {/* Gated on richness_z, not richness_label -- same fallback-label
                            issue fixed on Vol Scanner: "Neutral" is the documented
                            no-signal default, not a real reading, when richness_z is null. */}
                        {idea.richness_z !== null && idea.richness_label ? (
                          <span
                            className="inline-flex rounded-full px-2 py-0.5 text-xs font-medium"
                            style={{ backgroundColor: RICHNESS_BG[key], color: RICHNESS_TEXT[key] }}
                          >
                            {idea.richness_label}
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
