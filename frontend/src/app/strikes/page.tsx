"use client";

import { useEffect, useMemo, useState } from "react";

import { ChartCard } from "@/components/chart-card";
import { PageIntro } from "@/components/page-intro";
import { SiteHeader } from "@/components/site-header";
import { Skeleton } from "@/components/ui/skeleton";
import { StrikeBars, type StrikeDatum } from "@/components/charts/strike-bars";
import { api, ApiError, type StrikeProfileResponse } from "@/lib/api";
import { fmtInt, fmtNum } from "@/lib/format";
import { cn } from "@/lib/utils";
import { primarySymbol, useSettingsStore } from "@/lib/store";

function Stat({ label, value, sub, tone }: { label: string; value: string; sub?: string; tone?: string }) {
  return (
    <div className="flex min-w-[6rem] flex-col gap-0.5">
      <span className="text-[10px] uppercase tracking-[0.12em] text-muted-foreground">{label}</span>
      <span className={cn("font-mono text-base leading-none tabular-nums", tone ?? "text-foreground")}>{value}</span>
      {sub && <span className="text-[10px] text-muted-foreground">{sub}</span>}
    </div>
  );
}

export default function StrikesPage() {
  const symbols = useSettingsStore((s) => s.symbols);
  const symbol = primarySymbol(symbols);

  const [data, setData] = useState<StrikeProfileResponse | null>(null);
  const [expiration, setExpiration] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Reset the chosen expiration when the symbol changes -- last symbol's date
  // may not even be listed on this one.
  useEffect(() => {
    setExpiration(null);
  }, [symbol]);

  useEffect(() => {
    let cancelled = false;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- standard fetch-reset pattern, guarded by `cancelled`
    setLoading(true);
    setError(null);
    api
      .scannerStrikeProfile(symbol, expiration ?? undefined)
      .then((res) => !cancelled && setData(res))
      .catch(
        (err) =>
          !cancelled &&
          setError(
            err instanceof ApiError ? `Failed to load strike profile: ${err.message}` : "Failed to load strike profile."
          )
      )
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [symbol, expiration]);

  const rows = data?.strikes ?? [];

  const oi: StrikeDatum[] = useMemo(
    () => rows.map((r) => ({ strike: r.strike, call: r.call_oi, put: r.put_oi })),
    [rows]
  );

  // Gamma x OI: how much gamma actually sits at each strike, rather than the
  // per-contract gamma (which peaks at the money for every strike ladder and
  // says nothing about where positioning is).
  const gammaOi: StrikeDatum[] = useMemo(
    () =>
      rows.map((r) => ({
        strike: r.strike,
        call: r.call_gamma != null && r.call_oi != null ? r.call_gamma * r.call_oi : null,
        put: r.put_gamma != null && r.put_oi != null ? r.put_gamma * r.put_oi : null,
      })),
    [rows]
  );

  const stats = useMemo(() => {
    const callOi = rows.reduce((s, r) => s + (r.call_oi ?? 0), 0);
    const putOi = rows.reduce((s, r) => s + (r.put_oi ?? 0), 0);
    const total = (d: StrikeDatum) => (d.call ?? 0) + (d.put ?? 0);
    const peakOi = oi.length ? oi.reduce((a, b) => (total(b) > total(a) ? b : a)) : null;
    const peakGamma = gammaOi.length ? gammaOi.reduce((a, b) => (total(b) > total(a) ? b : a)) : null;
    return {
      callOi,
      putOi,
      pcr: callOi > 0 ? putOi / callOi : null,
      hasOi: callOi + putOi > 0,
      peakOiStrike: peakOi && total(peakOi) > 0 ? peakOi.strike : null,
      peakGammaStrike: peakGamma && total(peakGamma) > 0 ? peakGamma.strike : null,
    };
  }, [rows, oi, gammaOi]);

  const spot = data?.underlying_price ?? null;
  const distance = (strike: number | null) =>
    strike !== null && spot ? `${(((strike - spot) / spot) * 100).toFixed(1)}% from spot` : undefined;

  return (
    <>
      <SiteHeader title="Strike Profile" />
      <main className="flex-1 overflow-y-auto p-4 md:p-6">
        <PageIntro
          summary={<>Open interest and gamma concentration by strike for {symbol}.</>}
        >
          <p className="mb-2">
            Open interest is how many contracts are actually open at each strike — where positioning sits, not where
            it traded today. A strike carrying far more than its neighbours is where the market has committed.
          </p>
          <p className="mb-2">
            <span className="text-foreground">Gamma × OI</span> weights each strike&apos;s gamma by the contracts
            actually open there. Raw per-contract gamma always peaks at the money and so says nothing about
            positioning; weighting by open interest shows where hedging flow would concentrate if spot moved there —
            a candidate pinning level.
          </p>
          <p>
            Deliberately not labelled &quot;dealer gamma&quot; or given a sign. Open interest says how many contracts
            are open, never which side of them a dealer holds, so a signed positioning number would be an assumption
            wearing the data&apos;s precision. This is concentration, not direction.
          </p>
        </PageIntro>

        {error && (
          <div className="mb-4 rounded-md border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive">
            {error}
          </div>
        )}

        {loading && !data ? (
          <Skeleton className="h-96 w-full" />
        ) : !data || rows.length === 0 ? (
          <div className="rounded-lg border border-border bg-card px-4 py-8 text-center text-sm text-muted-foreground">
            No strike data for {symbol}.
          </div>
        ) : (
          <>
            <div className="mb-4 flex flex-wrap items-start gap-x-6 gap-y-3 rounded-lg border border-border bg-card px-4 py-3">
              <div className="flex flex-col gap-1">
                <span className="text-[10px] uppercase tracking-[0.12em] text-muted-foreground">Expiration</span>
                <select
                  value={data.expiration ?? ""}
                  onChange={(e) => setExpiration(e.target.value)}
                  className="rounded-md border border-border bg-background px-2 py-1 font-mono text-xs"
                >
                  {data.available_expirations.map((e) => (
                    <option key={e.expiration} value={e.expiration}>
                      {e.expiration} ({e.dte}d)
                    </option>
                  ))}
                </select>
              </div>
              <Stat label="Spot" value={fmtNum(spot, 2)} sub={`${rows.length} strikes`} />
              {stats.hasOi ? (
                <>
                  <Stat label="Call OI" value={fmtInt(stats.callOi)} tone="text-[var(--chart-1)]" />
                  <Stat label="Put OI" value={fmtInt(stats.putOi)} tone="text-neg" />
                  <Stat
                    label="Put/Call"
                    value={stats.pcr !== null ? fmtNum(stats.pcr, 2) : "—"}
                    sub={stats.pcr !== null ? (stats.pcr > 1 ? "more puts open" : "more calls open") : undefined}
                  />
                  <Stat
                    label="Peak OI"
                    value={stats.peakOiStrike !== null ? fmtNum(stats.peakOiStrike, 0) : "—"}
                    sub={distance(stats.peakOiStrike)}
                  />
                  <Stat
                    label="Peak Gamma"
                    value={stats.peakGammaStrike !== null ? fmtNum(stats.peakGammaStrike, 0) : "—"}
                    sub={distance(stats.peakGammaStrike)}
                  />
                </>
              ) : (
                <div className="flex-1 text-xs text-muted-foreground">
                  <span className="text-foreground">No open interest reported for {symbol}.</span> Schwab returns zero
                  OI on every contract for cash-settled index options — gamma is populated but unweightable, so the
                  charts below are empty by data availability, not by error. Single-name symbols report OI normally.
                </div>
              )}
            </div>

            <ChartCard
              title="Open Interest by Strike"
              hint="Contracts currently open at each listed strike. Bars, not a line: there is no value between two listed strikes, and a line would smooth away the single-strike concentrations worth noticing. Outlined bar marks the peak."
              className="mb-4"
            >
              <StrikeBars data={oi} spot={spot} peakStrike={stats.peakOiStrike} unitDigits={0} />
            </ChartCard>

            <ChartCard
              title="Gamma × Open Interest by Strike"
              hint="Each strike's gamma weighted by the contracts open there -- where hedging flow would concentrate if spot reached it. Magnitude only; open interest never reveals which side a dealer is on, so no direction is claimed."
            >
              <StrikeBars data={gammaOi} spot={spot} peakStrike={stats.peakGammaStrike} unitDigits={1} />
            </ChartCard>
          </>
        )}
      </main>
    </>
  );
}
