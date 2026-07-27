"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";

import { SiteHeader } from "@/components/site-header";
import { ChartCard } from "@/components/chart-card";
import { SmileChart } from "@/components/charts/smile-chart";
import { ContractTable } from "@/components/contract-table";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { api, ApiError, type ContractRow, type ExpiryResponse } from "@/lib/api";
import { fmtDate, fmtNum } from "@/lib/format";
import { useSettingsStore } from "@/lib/store";

export default function StrikeSelectorPage() {
  const params = useParams<{ symbol: string }>();
  const symbol = params.symbol.toUpperCase();
  const settings = useSettingsStore();

  const [expiryData, setExpiryData] = useState<ExpiryResponse | null>(null);
  const [selectedExpiration, setSelectedExpiration] = useState<string>("");
  const [typeChoice, setTypeChoice] = useState<"BOTH" | "CALL" | "PUT">("BOTH");
  const [contracts, setContracts] = useState<ContractRow[]>([]);
  const [selected, setSelected] = useState<ContractRow | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  // Load expirations + smile for the currently selected expiry.
  useEffect(() => {
    let cancelled = false;
    api
      .expiry(symbol, selectedExpiration)
      .then((res) => {
        if (cancelled) return;
        setExpiryData(res);
        setSelectedExpiration(res.expiration);
      })
      .catch((err) => !cancelled && setError(err instanceof ApiError ? err.message : "Failed to load expiries."));
    return () => {
      cancelled = true;
    };
    // Intentionally not depending on selectedExpiration to avoid refetching smile on every table change
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [symbol, settings.refreshNonce]);

  // Load scored contracts whenever the expiry/type/settings change.
  useEffect(() => {
    if (!selectedExpiration) return;
    let cancelled = false;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- standard fetch-reset pattern, guarded by `cancelled`
    setLoading(true);
    setError(null);
    api
      .contracts(symbol, {
        expiration: selectedExpiration,
        option_type: typeChoice,
        intent: settings.intent,
        target_delta: settings.targetDelta,
        delta_tolerance: settings.deltaTolerance,
        w_value: settings.weightValue,
        w_delta_fit: settings.weightDeltaFit,
        w_liquidity: settings.weightLiquidity,
        min_volume: settings.minVolume,
        min_open_interest: settings.minOpenInterest,
        max_spread_pct: settings.maxSpreadPct,
      })
      .then((res) => {
        if (cancelled) return;
        setContracts(res.contracts);
        setSelected(res.contracts[0] ?? null);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err instanceof ApiError ? err.message : "Failed to load contracts.");
        setContracts([]);
      })
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [
    symbol,
    selectedExpiration,
    typeChoice,
    settings.intent,
    settings.targetDelta,
    settings.deltaTolerance,
    settings.weightValue,
    settings.weightDeltaFit,
    settings.weightLiquidity,
    settings.minVolume,
    settings.minOpenInterest,
    settings.maxSpreadPct,
    settings.refreshNonce,
  ]);

  const handleExpirationChange = (expiration: string) => {
    setSelectedExpiration(expiration);
    api
      .expiry(symbol, expiration)
      .then(setExpiryData)
      .catch(() => {});
  };

  return (
    <>
      <SiteHeader title={`Strike Selector — ${symbol}`} />
      <main className="flex-1 overflow-y-auto p-4 md:p-6">
        {error && (
          <div className="mb-4 rounded-md border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive">
            {error}
          </div>
        )}

        <div className="mb-4 flex flex-wrap gap-3">
          <Select value={selectedExpiration} onValueChange={handleExpirationChange}>
            <SelectTrigger className="w-64">
              <SelectValue placeholder="Select expiry" />
            </SelectTrigger>
            <SelectContent>
              {(expiryData?.expirations ?? []).map((e) => (
                <SelectItem key={e.expiration} value={e.expiration}>
                  {fmtDate(e.expiration)} (DTE {e.dte})
                </SelectItem>
              ))}
            </SelectContent>
          </Select>

          <Select value={typeChoice} onValueChange={(v) => setTypeChoice(v as "BOTH" | "CALL" | "PUT")}>
            <SelectTrigger className="w-32">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="BOTH">Both</SelectItem>
              <SelectItem value="CALL">Call</SelectItem>
              <SelectItem value="PUT">Put</SelectItem>
            </SelectContent>
          </Select>

          {expiryData?.underlying_price != null && (
            <span className="flex items-center font-mono text-sm tabular-nums text-muted-foreground">
              Spot ${fmtNum(expiryData.underlying_price, 2)}
            </span>
          )}
        </div>

        <div className="flex flex-col gap-4">
          <ChartCard title="Scored Contracts" bodyClassName="p-0 pt-3">
            <div className="px-3 pb-3">
              <p className="mb-2 text-xs text-muted-foreground">
                Click a row to highlight it on the smile below. Click a header to sort.
              </p>
              {loading ? (
                <Skeleton className="h-64 w-full" />
              ) : (
                <ContractTable
                  rows={contracts}
                  selectedRank={selected?.rank}
                  onSelectRow={setSelected}
                  hideColumns={["expiration", "dte"]}
                />
              )}
            </div>
          </ChartCard>

          <ChartCard title={selected ? `Smile — highlighting ${selected.optionType} ${selected.strikePrice}` : "Smile"}>
            {expiryData ? (
              <SmileChart points={expiryData.smile} highlight={selected} />
            ) : (
              <Skeleton className="h-72 w-full" />
            )}
          </ChartCard>
        </div>
      </main>
    </>
  );
}
