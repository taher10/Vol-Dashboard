import Link from "next/link";

import { fmtNum, fmtPct } from "@/lib/format";
import { COLOR_CALL, COLOR_PUT } from "@/lib/theme";
import type { Commentary } from "@/lib/api";

/**
 * Shared "what this means / is there a trade here" panel for Overview and
 * Expiry Drilldown -- one synthesized read per page rather than a caption
 * under every individual chart, matching how a trader actually reasons
 * about a vol surface (skew + richness together, not four separate facts).
 * example_trade (when present) is real strategy_engine output, computed the
 * same way Strategy Builder's own recommendation is -- never a separate,
 * hand-estimated number that could drift out of sync.
 */
export function InsightPanel({ commentary, symbol }: { commentary: Commentary; symbol: string }) {
  const trade = commentary.example_trade;

  return (
    <div className="flex flex-col gap-2 rounded-lg border border-border bg-accent/60 px-4 py-3 text-sm text-accent-foreground">
      <p className="font-semibold">{commentary.headline}</p>
      <p className="text-accent-foreground/90">{commentary.interpretation}</p>
      <p className="text-accent-foreground/90">{commentary.trade_angle}</p>

      {trade && (
        <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1.5 rounded-md border border-border/60 bg-card/60 px-3 py-2 text-xs">
          <span className="font-semibold text-foreground">{trade.structure}</span>
          <span className="flex flex-wrap items-center gap-x-2 text-muted-foreground">
            {trade.legs.map((leg, i) => (
              <span key={i}>
                {leg.action === "buy" ? "+" : "−"}
                <span style={{ color: leg.optionType === "CALL" ? COLOR_CALL : COLOR_PUT }}>{leg.optionType}</span>{" "}
                {fmtNum(leg.strike, leg.strike >= 1000 ? 0 : 1)}
              </span>
            ))}
          </span>
          <span className="text-[#0b5c0b]">Max P {fmtNum(trade.max_profit, 2)}</span>
          <span className="text-[#8f2323]">Max L {fmtNum(trade.max_loss, 2)}</span>
          <span className="text-muted-foreground">POP~ {fmtPct(trade.approx_pop * 100, 0)}</span>
          <Link
            href={`/strategy/${symbol}`}
            className="ml-auto whitespace-nowrap font-medium text-foreground underline underline-offset-2 hover:no-underline"
          >
            Open in Strategy Builder →
          </Link>
        </div>
      )}
    </div>
  );
}
