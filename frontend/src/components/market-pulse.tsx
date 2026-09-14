"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { ArrowUpRight } from "lucide-react";

import { Skeleton } from "@/components/ui/skeleton";
import { api, type ScannerRow } from "@/lib/api";
import { fmtNum, fmtSigned } from "@/lib/format";
import { cn } from "@/lib/utils";

/** The state of the whole book, above the fold.
 *
 * The Overview used to open with four charts of whichever single symbol was
 * selected, while the pipeline tracks 24. So the first screen answered "what
 * is SPX doing" -- a question you can only ask once you already know which
 * symbol you care about. This answers the one you actually open the app with:
 * is the data current, is premium broadly rich or cheap today, and which two
 * or three names are far enough from the middle to be worth opening.
 *
 * Every symbol here links straight to its Strategy Builder, so the path from
 * "that one looks extreme" to "show me the trade" is one click rather than a
 * sidebar switch plus a page change. */

function freshness(asOf: string | null): { label: string; tone: string } {
  if (!asOf) return { label: "no data", tone: "text-neg" };
  const hours = (Date.now() - new Date(asOf).getTime()) / 36e5;
  if (hours < 24) return { label: `${Math.max(0, Math.round(hours))}h ago`, tone: "text-pos" };
  const days = Math.round(hours / 24);
  // Past a couple of days the numbers below describe a market that has moved
  // on, which is worth saying loudly rather than printing a quiet timestamp.
  return { label: `${days}d ago`, tone: days > 2 ? "text-neg" : "text-amber-400" };
}

function Metric({ label, value, sub, tone }: { label: string; value: string; sub?: string; tone?: string }) {
  return (
    <div className="flex min-w-[5.5rem] flex-col gap-0.5">
      <span className="text-[10px] uppercase tracking-[0.12em] text-muted-foreground">{label}</span>
      <span className={cn("font-mono text-lg leading-none tabular-nums", tone ?? "text-foreground")}>{value}</span>
      {sub && <span className="text-[10px] text-muted-foreground">{sub}</span>}
    </div>
  );
}

function ExtremeList({
  title,
  hint,
  rows,
  tone,
}: {
  title: string;
  hint: string;
  rows: ScannerRow[];
  tone: "pos" | "neg";
}) {
  return (
    <div className="min-w-0 flex-1">
      <div className="mb-1.5 flex items-baseline gap-2">
        <span className="text-[10px] font-medium uppercase tracking-[0.12em] text-muted-foreground">{title}</span>
        <span className="truncate text-[10px] text-muted-foreground/70">{hint}</span>
      </div>
      <div className="flex flex-col">
        {rows.length === 0 && <span className="py-1 text-xs text-muted-foreground">Nothing at an extreme today.</span>}
        {rows.map((r) => (
          <Link
            key={r.symbol}
            href={`/strategy/${r.symbol}`}
            className="group flex items-center gap-2 rounded px-1 py-1 transition-colors hover:bg-muted/50"
          >
            <span className="size-1.5 shrink-0 rounded-full" style={{ backgroundColor: r.color }} />
            <span className="w-14 shrink-0 font-mono text-xs font-medium">{r.symbol}</span>
            <span
              className={cn(
                "w-12 shrink-0 text-right font-mono text-xs tabular-nums",
                tone === "neg" ? "text-neg" : "text-pos"
              )}
            >
              {fmtSigned(r.richness_z, 1)}
            </span>
            <span className="w-16 shrink-0 text-right font-mono text-[11px] tabular-nums text-muted-foreground">
              IV {fmtNum(r.atm_iv, 1)}
            </span>
            <span className="hidden truncate text-[11px] text-muted-foreground/80 sm:inline">{r.skew_bias}</span>
            <ArrowUpRight className="ml-auto size-3 shrink-0 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100" />
          </Link>
        ))}
      </div>
    </div>
  );
}

export function MarketPulse() {
  const [rows, setRows] = useState<ScannerRow[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    api
      .scanner()
      .then((res) => !cancelled && setRows(res.rows))
      .catch(() => undefined)
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, []);

  const stats = useMemo(() => {
    const scored = rows.filter((r) => r.richness_z !== null);
    const ranks = rows.map((r) => r.iv_rank).filter((v): v is number => v !== null);
    const median = ranks.length
      ? [...ranks].sort((a, b) => a - b)[Math.floor(ranks.length / 2)]
      : null;
    const count = (label: string) =>
      rows.filter((r) => (r.richness_label ?? "").toLowerCase() === label).length;
    const byZ = [...scored].sort((a, b) => (b.richness_z ?? 0) - (a.richness_z ?? 0));
    // Median history depth travels with the median rank. An IV Rank of 0
    // means "lowest IV in recorded history", and today 15 of 24 symbols sit
    // exactly there -- true, but off ~16 scattered days with a two-week hole
    // in them, not off a year. Printing the rank without the depth beside it
    // is the same mistake the Overview stat card used to make.
    const depths = rows.map((r) => r.days_of_history).filter((v) => v > 0).sort((a, b) => a - b);
    const medianDepth = depths.length ? depths[Math.floor(depths.length / 2)] : null;
    return {
      median,
      medianDepth,
      rich: count("rich"),
      cheap: count("cheap"),
      neutral: count("neutral"),
      // Only surface names that are genuinely away from the middle. Showing a
      // "top 3" regardless would dress up a flat day as an opportunity.
      richest: byZ.filter((r) => (r.richness_z ?? 0) >= 1).slice(0, 3),
      cheapest: byZ.filter((r) => (r.richness_z ?? 0) <= -1).reverse().slice(0, 3),
      asOf: rows.find((r) => r.as_of)?.as_of ?? null,
      thin: rows.filter((r) => r.days_of_history < 10).length,
    };
  }, [rows]);

  if (loading) return <Skeleton className="mb-4 h-28 w-full" />;
  if (rows.length === 0) return null;

  const fresh = freshness(stats.asOf);

  return (
    <div className="mb-4 overflow-hidden rounded-lg border border-border bg-card">
      <div className="flex flex-wrap items-start gap-x-8 gap-y-3 border-b border-border/70 px-4 py-3">
        <Metric label="Symbols" value={String(rows.length)} sub={stats.thin ? `${stats.thin} thin history` : "all tracked"} />
        <Metric label="Snapshot" value={fresh.label} tone={fresh.tone} sub="data age" />
        <Metric
          label="IV Rank"
          value={stats.median !== null ? fmtNum(stats.median, 0) : "—"}
          sub={stats.medianDepth ? `median · off ~${stats.medianDepth} days` : "median of book"}
        />
        <div className="flex flex-col gap-0.5">
          <span className="text-[10px] uppercase tracking-[0.12em] text-muted-foreground">Premium</span>
          <div className="flex items-center gap-2 font-mono text-lg leading-none tabular-nums">
            <span className="text-neg">{stats.rich}</span>
            <span className="text-xs text-muted-foreground">/</span>
            <span className="text-muted-foreground">{stats.neutral}</span>
            <span className="text-xs text-muted-foreground">/</span>
            <span className="text-pos">{stats.cheap}</span>
          </div>
          <span className="text-[10px] text-muted-foreground">rich / neutral / cheap</span>
        </div>
      </div>

      <div className="flex flex-col gap-4 px-4 py-3 sm:flex-row sm:gap-8">
        <ExtremeList title="Richest" hint="premium looks expensive to sell" rows={stats.richest} tone="neg" />
        <ExtremeList title="Cheapest" hint="premium looks expensive to buy" rows={stats.cheapest} tone="pos" />
      </div>
    </div>
  );
}
