"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";

import { PayoffChart } from "@/components/charts/payoff-chart";
import { InfoHint } from "@/components/info-hint";
import { SiteHeader } from "@/components/site-header";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { api, ApiError, type TradeIdea } from "@/lib/api";
import { fmtDate, fmtNum, fmtPct } from "@/lib/format";
import {
  COLOR_CALL,
  COLOR_PUT,
  RICHNESS_BG,
  RICHNESS_HINT,
  RICHNESS_TEXT,
  SKEW_BIAS_HINT,
  richnessKey,
} from "@/lib/theme";

type SortKey = "reward_risk" | "approx_pop" | "max_profit" | "dte";
type DirectionFilter = "all" | "bullish" | "bearish";
type StructureFilter = "all" | "credit" | "debit";

const SORT_OPTIONS: { key: SortKey; label: string }[] = [
  { key: "reward_risk", label: "Reward:Risk" },
  { key: "approx_pop", label: "Approx POP" },
  { key: "max_profit", label: "Max Profit" },
  { key: "dte", label: "DTE" },
];

function IdeaCard({ idea }: { idea: TradeIdea }) {
  const key = richnessKey(idea.richness_label);
  return (
    <div className="flex flex-col gap-3 rounded-lg border border-border bg-card p-4 shadow-sm">
      <div className="flex items-start justify-between gap-2">
        <span className="flex items-center gap-2">
          <span className="size-2.5 shrink-0 rounded-full" style={{ backgroundColor: idea.color }} />
          <span className="text-lg font-semibold">{idea.symbol}</span>
          {/* Neutral styling, not green/red-by-direction -- those same hex values
              are already used below for Max Profit/Max Loss, where green/red mean
              profit/loss, not bullish/bearish. The structure name itself already
              says "Bull"/"Bear", so the pill doesn't need to re-encode direction. */}
          <span className="inline-flex rounded-full bg-muted px-2 py-0.5 text-xs font-medium text-foreground">
            {idea.structure}
          </span>
        </span>
        <span className="shrink-0 text-right text-xs text-muted-foreground">
          {idea.dte}d
          <br />
          {fmtDate(idea.expiration)}
        </span>
      </div>

      <PayoffChart payoff={idea.payoff} breakevens={idea.breakevens} spot={idea.underlying_price} height={180} />

      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
        {idea.legs.map((leg, i) => (
          <span key={i}>
            {leg.action === "buy" ? "+" : "−"}
            <span style={{ color: leg.optionType === "CALL" ? COLOR_CALL : COLOR_PUT }}>{leg.optionType}</span>{" "}
            {fmtNum(leg.strike, leg.strike >= 1000 ? 0 : 1)}
          </span>
        ))}
      </div>

      <div className="grid grid-cols-3 gap-3 border-t border-border pt-3 text-sm sm:grid-cols-5">
        <div>
          <div className="text-xs text-muted-foreground">{idea.is_credit ? "Net credit" : "Net debit"}</div>
          <div className="font-mono font-semibold">{fmtNum(Math.abs(idea.net_debit_credit), 2)}</div>
        </div>
        <div>
          <div className="text-xs text-muted-foreground">Max profit</div>
          <div className="font-mono font-semibold text-[#0b5c0b]">{fmtNum(idea.max_profit, 2)}</div>
        </div>
        <div>
          <div className="text-xs text-muted-foreground">Max loss</div>
          <div className="font-mono font-semibold text-[#8f2323]">{fmtNum(idea.max_loss, 2)}</div>
        </div>
        <div>
          <div className="text-xs text-muted-foreground">Reward:Risk</div>
          <div className="font-mono font-semibold">{idea.reward_risk != null ? fmtNum(idea.reward_risk, 2) : "—"}</div>
        </div>
        <div>
          <div className="text-xs text-muted-foreground">Approx POP</div>
          <div className="font-mono font-semibold">{fmtPct(idea.approx_pop * 100, 0)}</div>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs text-muted-foreground">
          Skew: {idea.has_wing_data ? idea.skew_bias : "—"}
        </span>
        {idea.richness_z !== null && idea.richness_label ? (
          <span
            className="inline-flex rounded-full px-2 py-0.5 text-xs font-medium"
            style={{ backgroundColor: RICHNESS_BG[key], color: RICHNESS_TEXT[key] }}
          >
            {idea.richness_label}
          </span>
        ) : (
          <span className="text-xs text-muted-foreground">Richness: —</span>
        )}
      </div>

      <p className="text-xs text-muted-foreground">{idea.reason}</p>

      <Link
        href={`/strategy/${idea.symbol}`}
        className="ml-auto text-xs font-medium text-foreground underline underline-offset-2 hover:no-underline"
      >
        Open in Strategy Builder →
      </Link>
    </div>
  );
}

export default function TradeIdeasPage() {
  const [data, setData] = useState<TradeIdea[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [directionFilter, setDirectionFilter] = useState<DirectionFilter>("all");
  const [structureFilter, setStructureFilter] = useState<StructureFilter>("all");
  const [sortKey, setSortKey] = useState<SortKey>("reward_risk");

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

  // Filter first, then sort descending by the chosen key -- nulls (only
  // possible on reward_risk, when max_loss is somehow 0) always sort last,
  // same convention as Vol Scanner.
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
      return bv - av;
    });
    return rows;
  }, [data, directionFilter, structureFilter, sortKey]);

  return (
    <>
      <SiteHeader title="Trade Ideas" />
      <main className="flex-1 overflow-y-auto p-4 md:p-6">
        <p className="mb-4 text-xs text-muted-foreground">
          One real example trade per symbol with a clear directional skew edge today -- same math Strategy Builder
          uses, not a separate estimate. Symbols with a balanced smile (no clear edge) don't appear here.
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
          <div className="flex flex-col gap-1.5">
            <span className="text-xs text-muted-foreground">Sort by</span>
            <Select value={sortKey} onValueChange={(v) => setSortKey(v as SortKey)}>
              <SelectTrigger size="sm" className="w-40">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {SORT_OPTIONS.map((o) => (
                  <SelectItem key={o.key} value={o.key}>
                    {o.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="ml-auto flex items-center gap-1 text-xs text-muted-foreground">
            Skew Bias <InfoHint text={SKEW_BIAS_HINT} /> · Richness <InfoHint text={RICHNESS_HINT} />
          </div>
        </div>

        {error && (
          <div className="mb-4 rounded-md border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive">
            {error}
          </div>
        )}

        {loading ? (
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            <Skeleton className="h-80 w-full" />
            <Skeleton className="h-80 w-full" />
          </div>
        ) : filteredSorted.length === 0 ? (
          <div className="rounded-md border border-border bg-muted px-4 py-3 text-sm text-muted-foreground">
            {data.length === 0
              ? "No directional trade ideas today -- every tracked symbol's smile is currently balanced, or there's no saved data yet."
              : "No ideas match the current filters."}
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            {filteredSorted.map((idea) => (
              <IdeaCard key={idea.symbol} idea={idea} />
            ))}
          </div>
        )}
      </main>
    </>
  );
}
