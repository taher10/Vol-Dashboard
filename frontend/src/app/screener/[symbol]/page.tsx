"use client";

import { useEffect, useMemo, useState } from "react";
import { useParams } from "next/navigation";

import { SiteHeader } from "@/components/site-header";
import { ChartCard } from "@/components/chart-card";
import { RichnessTable } from "@/components/richness-table";
import { ContractTable } from "@/components/contract-table";
import { CandidateBarChart } from "@/components/charts/candidate-bar-chart";
import { SmileChart } from "@/components/charts/smile-chart";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { Slider } from "@/components/ui/slider";
import { Skeleton } from "@/components/ui/skeleton";
import { api, ApiError, type ContractsResponse, type ExpiryResponse, type OverviewResponse } from "@/lib/api";
import { fmtNum } from "@/lib/format";
import { useSettingsStore } from "@/lib/store";

export default function DecisionScreenerPage() {
  const params = useParams<{ symbol: string }>();
  const symbol = params.symbol.toUpperCase();
  const settings = useSettingsStore();

  const [overview, setOverview] = useState<OverviewResponse | null>(null);
  const [contractsRes, setContractsRes] = useState<ContractsResponse | null>(null);
  const [topSmile, setTopSmile] = useState<ExpiryResponse | null>(null);
  const [onlyMatching, setOnlyMatching] = useState(false);
  const [nCandidates, setNCandidates] = useState(10);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    api
      .overview([symbol], settings.dteRange[0], settings.dteRange[1])
      .then((res) => !cancelled && setOverview(res))
      .catch(() => !cancelled && setOverview(null));
    return () => {
      cancelled = true;
    };
  }, [symbol, settings.dteRange, settings.refreshNonce]);

  useEffect(() => {
    let cancelled = false;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- standard fetch-reset pattern, guarded by `cancelled`
    setLoading(true);
    setError(null);
    api
      .contracts(symbol, {
        option_type: settings.optionTypes,
        intent: settings.intent,
        target_delta: settings.targetDelta,
        delta_tolerance: settings.deltaTolerance,
        w_value: settings.weightValue,
        w_delta_fit: settings.weightDeltaFit,
        w_liquidity: settings.weightLiquidity,
        dte_min: settings.dteRange[0],
        dte_max: settings.dteRange[1],
        min_volume: settings.minVolume,
        min_open_interest: settings.minOpenInterest,
        max_spread_pct: settings.maxSpreadPct,
      })
      .then((res) => {
        if (cancelled) return;
        setContractsRes(res);
        const top = res.contracts[0];
        if (top) {
          api
            .expiry(symbol, top.expiration)
            .then((e) => !cancelled && setTopSmile(e))
            .catch(() => {});
        } else {
          setTopSmile(null);
        }
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err instanceof ApiError ? err.message : "Failed to load screener data.");
        setContractsRes(null);
      })
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [
    symbol,
    settings.optionTypes,
    settings.intent,
    settings.targetDelta,
    settings.deltaTolerance,
    settings.weightValue,
    settings.weightDeltaFit,
    settings.weightLiquidity,
    settings.dteRange,
    settings.minVolume,
    settings.minOpenInterest,
    settings.maxSpreadPct,
    settings.refreshNonce,
  ]);

  const intentRichness = settings.intent === "buy" ? "Cheap" : "Rich";
  const richnessRows = useMemo(() => {
    const rows = overview?.expiry_scores ?? [];
    return onlyMatching ? rows.filter((r) => r.richness_label === intentRichness) : rows;
  }, [overview, onlyMatching, intentRichness]);

  const topRows = (contractsRes?.contracts ?? []).slice(0, nCandidates);
  const topContract = contractsRes?.contracts?.[0];

  return (
    <>
      <SiteHeader title={`Decision Screener — ${symbol}`} />
      <main className="flex-1 overflow-y-auto p-4 md:p-6">
        {error && (
          <div className="mb-4 rounded-md border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive">
            {error}
          </div>
        )}

        <div className="flex flex-col gap-4">
          <ChartCard title="Expiry Context" bodyClassName="p-0 pt-3">
            <div className="flex items-center gap-2 px-3 pb-2">
              <Checkbox id="only-matching" checked={onlyMatching} onCheckedChange={(v) => setOnlyMatching(!!v)} />
              <Label htmlFor="only-matching" className="text-xs text-muted-foreground">
                Only show expiries matching my intent ({intentRichness})
              </Label>
            </div>
            <div className="px-3 pb-3">
              <RichnessTable rows={richnessRows} />
            </div>
          </ChartCard>

          <ChartCard title="Top Candidates" bodyClassName="p-0 pt-3">
            <div className="flex items-center gap-3 px-3 pb-3">
              <Label className="whitespace-nowrap text-xs text-muted-foreground">
                Candidates: {nCandidates}
              </Label>
              <Slider
                className="max-w-xs"
                min={1}
                max={50}
                step={1}
                value={[nCandidates]}
                onValueChange={([v]) => setNCandidates(v)}
              />
            </div>
            <div className="px-3 pb-3">
              {loading ? <Skeleton className="h-64 w-full" /> : <CandidateBarChart rows={topRows} />}
            </div>
          </ChartCard>

          <ChartCard title="Full Detail Table" bodyClassName="p-0 pt-3">
            <div className="px-3 pb-3">
              <p className="mb-2 text-xs text-muted-foreground">
                {contractsRes?.total_matched ?? 0} contract(s) match your current filters across all expiries.
              </p>
              {loading ? <Skeleton className="h-64 w-full" /> : <ContractTable rows={contractsRes?.contracts ?? []} />}
            </div>
          </ChartCard>

          {topContract && (
            <ChartCard
              title={`Top Candidate — Smile Context (#1: ${topContract.optionType} ${topContract.strikePrice}, score ${fmtNum(
                topContract.composite_score,
                1
              )})`}
            >
              {topSmile ? (
                <SmileChart points={topSmile.smile} highlight={topContract} />
              ) : (
                <Skeleton className="h-72 w-full" />
              )}
            </ChartCard>
          )}
        </div>
      </main>
    </>
  );
}
