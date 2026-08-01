"use client";

import { useEffect, useState } from "react";
import { useParams, useRouter, useSearchParams } from "next/navigation";

import { SiteHeader } from "@/components/site-header";
import { ChartCard } from "@/components/chart-card";
import { InsightPanel } from "@/components/insight-panel";
import { StatCard } from "@/components/stat-card";
import { SmileChart } from "@/components/charts/smile-chart";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { api, ApiError, type ExpiryResponse, type ZScore } from "@/lib/api";
import { fmtDate, fmtNum, fmtSigned } from "@/lib/format";
import { RICHNESS_BG, RICHNESS_TEXT, richnessKey } from "@/lib/theme";
import { useSettingsStore } from "@/lib/store";

function zscoreSub(z: ZScore | null): string | undefined {
  if (!z) return "Needs 3+ sessions at this DTE for context";
  return `z=${fmtSigned(z.zscore, 1)} vs. trailing avg — ${Math.abs(z.zscore) >= 1.5 ? "unusual" : "typical"} for this name`;
}

export default function ExpiryDrilldownPage() {
  const params = useParams<{ symbol: string }>();
  const symbol = params.symbol.toUpperCase();
  const searchParams = useSearchParams();
  const router = useRouter();
  const urlExpiration = searchParams.get("expiration") ?? undefined;
  const refreshNonce = useSettingsStore((s) => s.refreshNonce);

  const [data, setData] = useState<ExpiryResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [ivZ, setIvZ] = useState<ZScore | null>(null);
  const [skewZ, setSkewZ] = useState<ZScore | null>(null);
  const [curvatureZ, setCurvatureZ] = useState<ZScore | null>(null);

  useEffect(() => {
    let cancelled = false;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- standard fetch-reset pattern, guarded by `cancelled`
    setLoading(true);
    setError(null);
    api
      .expiry(symbol, urlExpiration)
      .then((res) => !cancelled && setData(res))
      .catch((err) => {
        if (cancelled) return;
        setError(err instanceof ApiError ? err.message : "Failed to load expiry data.");
        setData(null);
      })
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [symbol, urlExpiration, refreshNonce]);

  // Trailing z-score for the metrics above, computed at THIS expiry's actual
  // DTE (not a fixed default) so "is -0.55 normal for this name" is answered
  // apples-to-apples with what's on screen, not some other maturity.
  useEffect(() => {
    const dte = data?.score?.dte;
    if (dte == null) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- resetting derived state when its input disappears, not a fetch-in-progress race
      setIvZ(null);
      setSkewZ(null);
      setCurvatureZ(null);
      return;
    }
    let cancelled = false;
    Promise.all([
      api.zscore(symbol, "atm_iv", dte).catch(() => null),
      api.zscore(symbol, "skew", dte).catch(() => null),
      api.zscore(symbol, "curvature", dte).catch(() => null),
    ]).then(([iv, skew, curvature]) => {
      if (cancelled) return;
      setIvZ(iv);
      setSkewZ(skew);
      setCurvatureZ(curvature);
    });
    return () => {
      cancelled = true;
    };
  }, [symbol, data?.score?.dte]);

  const handleExpirationChange = (expiration: string) => {
    router.push(`/expiry/${symbol}?expiration=${encodeURIComponent(expiration)}`);
  };

  const score = data?.score;
  const key = richnessKey(score?.richness_label);

  return (
    <>
      <SiteHeader title={`Expiry Drilldown — ${symbol}`} />
      <main className="flex-1 overflow-y-auto p-4 md:p-6">
        {error && (
          <div className="mb-4 rounded-md border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive">
            {error}
          </div>
        )}

        {loading && !data ? (
          <Skeleton className="h-96 w-full" />
        ) : data ? (
          <div className="flex flex-col gap-4">
            <div className="flex flex-wrap items-center gap-3">
              <Select value={data.expiration} onValueChange={handleExpirationChange}>
                <SelectTrigger className="w-64">
                  <SelectValue placeholder="Select expiry" />
                </SelectTrigger>
                <SelectContent>
                  {data.expirations.map((e) => (
                    <SelectItem key={e.expiration} value={e.expiration}>
                      {fmtDate(e.expiration)} (DTE {e.dte})
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {data.underlying_price != null && (
                <span className="font-mono text-sm tabular-nums text-muted-foreground">
                  Spot ${fmtNum(data.underlying_price, 2)}
                </span>
              )}
            </div>

            {score && (
              <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
                <StatCard
                  label="ATM IV"
                  value={fmtNum(score.atm_iv, 2)}
                  hint="At-the-money implied volatility for this expiry."
                  sub={zscoreSub(ivZ)}
                />
                <StatCard
                  label="Skew"
                  value={fmtNum(score.skew, 2)}
                  hint="IV(25Δ put) − IV(25Δ call). Positive means puts are priced richer than calls."
                  sub={zscoreSub(skewZ)}
                />
                <StatCard
                  label="Curvature"
                  value={fmtNum(score.curvature, 2)}
                  hint="Butterfly: average of 25Δ put/call IV minus ATM IV — how much the wings are priced up relative to the middle."
                  sub={zscoreSub(curvatureZ)}
                />
                <StatCard
                  label="Richness"
                  value={score.richness_label}
                  accent={key === "cheap" ? "good" : key === "rich" ? "critical" : "neutral"}
                  sub={score.skew_bias}
                  hint={`This expiry's IV vs. its own ${
                    score.richness_basis === "vrp" ? "realized vol" : "historical IV range"
                  }. Rich favors selling premium, Cheap favors buying.`}
                />
              </div>
            )}

            {data.commentary && <InsightPanel commentary={data.commentary} symbol={symbol} />}

            <ChartCard title={`Volatility Smile — ${fmtDate(data.expiration)}`}>
              <SmileChart points={data.smile} />
            </ChartCard>

            {data.neighbors.length > 0 && (
              <ChartCard title="ATM IV vs. Neighboring Expiries" bodyClassName="p-0 pt-3">
                <div className="px-3 pb-3">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>Position</TableHead>
                        <TableHead>Expiration</TableHead>
                        <TableHead className="text-right">DTE</TableHead>
                        <TableHead className="text-right">ATM IV</TableHead>
                        <TableHead>Richness</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {data.neighbors.map((n) => {
                        const nKey = richnessKey(n.richness_label);
                        return (
                          <TableRow key={n.position} className={n.position === "selected" ? "bg-accent/50" : ""}>
                            <TableCell className="capitalize text-muted-foreground">{n.position}</TableCell>
                            <TableCell className="font-medium">{fmtDate(n.expiration)}</TableCell>
                            <TableCell className="text-right font-mono tabular-nums">{n.dte}</TableCell>
                            <TableCell className="text-right font-mono tabular-nums">{fmtNum(n.atm_iv, 2)}</TableCell>
                            <TableCell>
                              <span
                                className="inline-flex rounded-full px-2 py-0.5 text-xs font-medium"
                                style={{ backgroundColor: RICHNESS_BG[nKey], color: RICHNESS_TEXT[nKey] }}
                              >
                                {n.richness_label}
                              </span>
                            </TableCell>
                          </TableRow>
                        );
                      })}
                    </TableBody>
                  </Table>
                </div>
              </ChartCard>
            )}
          </div>
        ) : null}
      </main>
    </>
  );
}
