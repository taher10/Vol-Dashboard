"use client";

import { useMemo } from "react";

import { cn } from "@/lib/utils";
import type { SurfaceCell } from "@/lib/api";

/** The volatility surface as a heatmap: moneyness across, expiry down, colour
 * is implied vol.
 *
 * A surface is a 3D object and the usual instinct is to render it as one.
 * Rotatable 3D plots look impressive and are worse to read: occlusion hides
 * the wing you care about, and comparing two cells means judging height by
 * eye. A heatmap keeps every point visible and comparable at once, which is
 * what you actually do with a surface -- find where vol is dislocated
 * relative to its neighbours.
 *
 * Empty cells render as empty. A gap means no contract was quoted there, and
 * interpolating it would invent a volatility the market never printed. */

/** Blue (cheap) through to amber/red (expensive). Deliberately not a rainbow:
 * a perceptually ordered ramp means "further right on the legend" reads as
 * "more expensive" without consulting it. */
function ivColor(iv: number, min: number, max: number): string {
  const t = max > min ? (iv - min) / (max - min) : 0.5;
  const stops: [number, [number, number, number]][] = [
    [0.0, [37, 78, 122]],
    [0.35, [58, 132, 152]],
    [0.6, [201, 176, 92]],
    [0.8, [214, 122, 63]],
    [1.0, [214, 71, 84]],
  ];
  let lo = stops[0];
  let hi = stops[stops.length - 1];
  for (let i = 0; i < stops.length - 1; i++) {
    if (t >= stops[i][0] && t <= stops[i + 1][0]) {
      lo = stops[i];
      hi = stops[i + 1];
      break;
    }
  }
  const span = hi[0] - lo[0] || 1;
  const k = (t - lo[0]) / span;
  const c = lo[1].map((v, i) => Math.round(v + (hi[1][i] - v) * k));
  return `rgb(${c[0]}, ${c[1]}, ${c[2]})`;
}

export function VolSurfaceHeatmap({
  cells,
  expirations,
  moneyness,
  ivMin,
  ivMax,
  selectedDte,
  onSelectDte,
}: {
  cells: SurfaceCell[];
  expirations: { dte: number; expiration: string }[];
  moneyness: number[];
  ivMin: number;
  ivMax: number;
  selectedDte: number | null;
  onSelectDte: (dte: number) => void;
}) {
  const lookup = useMemo(() => {
    const m = new Map<string, SurfaceCell>();
    for (const c of cells) m.set(`${c.dte}|${c.moneyness}`, c);
    return m;
  }, [cells]);

  if (cells.length === 0) {
    return <div className="py-8 text-center text-sm text-muted-foreground">No quoted contracts to build a surface.</div>;
  }

  return (
    <div className="overflow-x-auto">
      <div className="min-w-max">
        <div className="mb-1 flex items-end gap-px pl-12">
          {moneyness.map((m) => (
            <div key={m} className="w-6 shrink-0 text-center text-[9px] text-muted-foreground">
              {/* Only label the anchors: every 2.5% bucket labelled is noise. */}
              {Math.abs(m) < 1e-9 ? "ATM" : Math.abs(Math.round((m * 1000) % 100)) < 1e-9 ? `${(m * 100).toFixed(0)}%` : ""}
            </div>
          ))}
        </div>

        {expirations.map((e) => (
          <div key={e.dte} className="flex items-center gap-px">
            <button
              type="button"
              onClick={() => onSelectDte(e.dte)}
              className={cn(
                "w-12 shrink-0 pr-1 text-right font-mono text-[10px] transition-colors",
                selectedDte === e.dte ? "font-medium text-foreground" : "text-muted-foreground hover:text-foreground"
              )}
            >
              {e.dte}d
            </button>
            {moneyness.map((m) => {
              const cell = lookup.get(`${e.dte}|${m}`);
              return (
                <div
                  key={m}
                  onClick={() => onSelectDte(e.dte)}
                  title={
                    cell
                      ? `${e.expiration} (${e.dte}d) · ${(m * 100).toFixed(1)}% · IV ${cell.iv.toFixed(1)} · ${cell.side}`
                      : `${e.expiration} (${e.dte}d) · ${(m * 100).toFixed(1)}% · not quoted`
                  }
                  className={cn(
                    "h-5 w-6 shrink-0 cursor-pointer rounded-[1px] transition-opacity",
                    selectedDte !== null && selectedDte !== e.dte && "opacity-40"
                  )}
                  style={{
                    backgroundColor: cell ? ivColor(cell.iv, ivMin, ivMax) : "transparent",
                    outline: cell ? undefined : "1px dashed rgba(255,255,255,0.05)",
                    outlineOffset: "-1px",
                  }}
                />
              );
            })}
          </div>
        ))}

        <div className="mt-3 flex items-center gap-2 pl-12 text-[10px] text-muted-foreground">
          <span className="font-mono">{ivMin.toFixed(0)}</span>
          <span className="flex h-2 w-40 overflow-hidden rounded-sm">
            {Array.from({ length: 40 }, (_, i) => (
              <span key={i} className="flex-1" style={{ backgroundColor: ivColor(i / 39, 0, 1) }} />
            ))}
          </span>
          <span className="font-mono">{ivMax.toFixed(0)}</span>
          <span className="ml-1">implied vol · puts left of ATM, calls right</span>
        </div>
      </div>
    </div>
  );
}
