"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";

import { SiteHeader } from "@/components/site-header";
import { ChartCard } from "@/components/chart-card";
import { PayoffChart } from "@/components/charts/payoff-chart";
import { InfoHint } from "@/components/info-hint";
import { TradeCard } from "@/components/trade-card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { api, ApiError, type RecommendResponse } from "@/lib/api";
import { fmtNum } from "@/lib/format";

type Direction = "bullish" | "bearish";
type Timeline = "short" | "medium" | "long";
type Risk = "conservative" | "moderate" | "aggressive";

export default function StrategyBuilderPage() {
  const params = useParams<{ symbol: string }>();
  const symbol = params.symbol.toUpperCase();

  const [direction, setDirection] = useState<Direction>("bullish");
  const [timeline, setTimeline] = useState<Timeline>("medium");
  const [risk, setRisk] = useState<Risk>("moderate");
  const [capitalInput, setCapitalInput] = useState("10000");
  const [debouncedCapitalInput, setDebouncedCapitalInput] = useState(capitalInput);

  const [data, setData] = useState<RecommendResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  // Debounce the capital input so typing a dollar amount doesn't refetch on every keystroke.
  useEffect(() => {
    const timer = setTimeout(() => setDebouncedCapitalInput(capitalInput), 400);
    return () => clearTimeout(timer);
  }, [capitalInput]);

  const capitalValue = Number(debouncedCapitalInput);
  const capital = Number.isFinite(capitalValue) && capitalValue > 0 ? capitalValue : undefined;

  useEffect(() => {
    let cancelled = false;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- standard fetch-reset pattern, guarded by `cancelled`
    setLoading(true);
    setError(null);
    api
      .recommendStrategy(symbol, { direction, timeline, risk, capital })
      .then((res) => {
        if (cancelled) return;
        setData(res);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err instanceof ApiError ? err.message : "Failed to load recommendation.");
        setData(null);
      })
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [symbol, direction, timeline, risk, capital]);

  const rec = data?.recommendation ?? null;
  const sizing = data?.sizing ?? null;

  return (
    <>
      <SiteHeader title={`Strategy Builder — ${symbol}`} />
      <main className="flex-1 overflow-y-auto p-4 md:p-6">
        {error && (
          <div className="mb-4 rounded-md border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive">
            {error}
          </div>
        )}

        <div className="mb-4 flex flex-col gap-4 rounded-lg border border-border bg-card p-4">
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <div className="flex flex-col gap-1.5">
              <Label className="text-xs text-muted-foreground">Direction</Label>
              <ToggleGroup
                type="single"
                value={direction}
                onValueChange={(v) => v && setDirection(v as Direction)}
                className="w-full"
              >
                <ToggleGroupItem value="bullish" className="flex-1">
                  Bullish
                </ToggleGroupItem>
                <ToggleGroupItem value="bearish" className="flex-1">
                  Bearish
                </ToggleGroupItem>
              </ToggleGroup>
            </div>

            <div className="flex flex-col gap-1.5">
              <Label className="flex items-center gap-1 text-xs text-muted-foreground">
                Timeline
                <InfoHint text="How soon you expect the move to play out — picks which expiries the trade is built from. Short ≈ 1-3 weeks, Medium ≈ 3-6 weeks, Long ≈ 6-13 weeks." />
              </Label>
              <ToggleGroup
                type="single"
                value={timeline}
                onValueChange={(v) => v && setTimeline(v as Timeline)}
                className="w-full"
              >
                <ToggleGroupItem value="short" className="flex-1">
                  Short
                </ToggleGroupItem>
                <ToggleGroupItem value="medium" className="flex-1">
                  Medium
                </ToggleGroupItem>
                <ToggleGroupItem value="long" className="flex-1">
                  Long
                </ToggleGroupItem>
              </ToggleGroup>
            </div>

            <div className="flex flex-col gap-1.5">
              <Label className="flex items-center gap-1 text-xs text-muted-foreground">
                Risk appetite
                <InfoHint text="Conservative: further out-of-the-money, sells premium — higher odds of a smaller win. Aggressive: closer to the money, buys premium — lower odds of a bigger win." />
              </Label>
              <ToggleGroup type="single" value={risk} onValueChange={(v) => v && setRisk(v as Risk)} className="w-full">
                <ToggleGroupItem value="conservative" className="flex-1">
                  Conservative
                </ToggleGroupItem>
                <ToggleGroupItem value="moderate" className="flex-1">
                  Moderate
                </ToggleGroupItem>
                <ToggleGroupItem value="aggressive" className="flex-1">
                  Aggressive
                </ToggleGroupItem>
              </ToggleGroup>
            </div>

            <div className="flex flex-col gap-1.5">
              <Label className="flex items-center gap-1 text-xs text-muted-foreground">
                Capital available
                <InfoHint text="Used to size the position — how many contracts of the recommended trade your capital covers, at the standard 100-share multiplier." />
              </Label>
              <div className="relative">
                <span className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-sm text-muted-foreground">
                  $
                </span>
                <Input
                  type="number"
                  min={0}
                  className="pl-6"
                  value={capitalInput}
                  onChange={(e) => setCapitalInput(e.target.value)}
                />
              </div>
            </div>
          </div>

          {data?.spot != null && (
            <span className="font-mono text-sm tabular-nums text-muted-foreground">Spot ${fmtNum(data.spot, 2)}</span>
          )}
        </div>

        <div className="grid grid-cols-1 gap-4 lg:grid-cols-[1fr_1.3fr]">
          <ChartCard title="Recommended Trade">
            {loading ? (
              <Skeleton className="h-64 w-full" />
            ) : !rec ? (
              <p className="py-6 text-center text-sm text-muted-foreground">
                No {direction} trade found for this timeline on {symbol}. Try a different timeline or risk appetite.
              </p>
            ) : (
              <div className="flex flex-col gap-3">
                <TradeCard candidate={rec} />

                {sizing ? (
                  <div className="rounded-md border border-border bg-accent/40 px-3 py-2 text-sm">
                    <div className="font-semibold">
                      Suggested position: {sizing.contracts} contract{sizing.contracts !== 1 ? "s" : ""}
                    </div>
                    <div className="text-xs text-muted-foreground">
                      {`Uses $${fmtNum(sizing.capital_used, 0)} of $${fmtNum(sizing.capital_available, 0)} (${fmtNum(
                        sizing.capital_used_pct,
                        0
                      )}%) · total max profit $${fmtNum(sizing.total_max_profit, 0)} · total max loss $${fmtNum(
                        sizing.total_max_loss,
                        0
                      )}`}
                    </div>
                  </div>
                ) : (
                  capital != null && (
                    <p className="text-xs text-muted-foreground">
                      {`$${fmtNum(capital, 0)} isn't enough to cover even 1 contract of this trade (max loss $${fmtNum(
                        rec.max_loss * 100,
                        0
                      )}/contract) — try a smaller symbol, a narrower risk profile, or more capital.`}
                    </p>
                  )
                )}

                {data?.commentary && (
                  <div className="rounded-md border border-border bg-accent/60 px-3 py-2 text-sm text-accent-foreground">
                    {data.commentary}
                  </div>
                )}
              </div>
            )}
          </ChartCard>

          <ChartCard
            title={rec ? `Payoff — ${rec.structure}` : "Payoff at Expiration"}
            hint="P/L per contract if held to expiration. Approx POP uses delta as a rough probability proxy, not a priced probability model."
          >
            {loading ? (
              <Skeleton className="h-72 w-full" />
            ) : rec ? (
              <PayoffChart payoff={rec.payoff} breakevens={rec.breakevens} spot={data?.spot ?? null} />
            ) : (
              <p className="py-6 text-center text-sm text-muted-foreground">No recommendation to plot.</p>
            )}
          </ChartCard>
        </div>
      </main>
    </>
  );
}
