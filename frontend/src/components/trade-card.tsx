import type { StrategyCandidate } from "@/lib/api";
import { fmtDate, fmtNum, fmtPct } from "@/lib/format";
import { COLOR_CALL, COLOR_PUT } from "@/lib/theme";
import { cn } from "@/lib/utils";

/**
 * Structure / legs / net credit-debit / max profit-loss / breakeven / POP
 * card -- shared by Strategy Builder's "Recommended Trade" and Backtest's
 * "Entry Trade", so both render identically instead of drifting apart.
 */
export function TradeCard({ candidate }: { candidate: StrategyCandidate }) {
  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <span className="text-lg font-semibold">{candidate.structure}</span>
        <span className="text-sm text-muted-foreground">
          {fmtDate(candidate.expiration)} · {candidate.dte}d
        </span>
      </div>

      <div className="flex flex-col gap-1">
        {candidate.legs.map((leg, i) => (
          <div key={i} className="flex items-center gap-2 text-sm">
            <span
              className={cn(
                "w-10 font-mono text-xs font-semibold",
                leg.action === "buy" ? "text-[#0b5c0b]" : "text-[#8f2323]"
              )}
            >
              {leg.action === "buy" ? "BUY" : "SELL"}
            </span>
            <span className="font-semibold" style={{ color: leg.optionType === "CALL" ? COLOR_CALL : COLOR_PUT }}>
              {leg.optionType}
            </span>
            <span className="font-mono">{fmtNum(leg.strike, leg.strike >= 1000 ? 0 : 1)}</span>
            <span className="ml-auto text-xs text-muted-foreground">
              delta {fmtNum(leg.delta, 2)} · ${fmtNum(leg.mid, 2)}
            </span>
          </div>
        ))}
      </div>

      <div className="grid grid-cols-2 gap-3 border-t border-border pt-3 text-sm sm:grid-cols-4">
        <div>
          <div className="text-xs text-muted-foreground">
            {candidate.net_debit_credit >= 0 ? "Net debit" : "Net credit"}
          </div>
          <div className="font-mono font-semibold">{fmtNum(Math.abs(candidate.net_debit_credit), 2)}</div>
        </div>
        <div>
          <div className="text-xs text-muted-foreground">Max profit</div>
          <div className="font-mono font-semibold text-[#0b5c0b]">{fmtNum(candidate.max_profit, 2)}</div>
        </div>
        <div>
          <div className="text-xs text-muted-foreground">Max loss</div>
          <div className="font-mono font-semibold text-[#8f2323]">{fmtNum(candidate.max_loss, 2)}</div>
        </div>
        <div>
          <div className="text-xs text-muted-foreground">Approx. POP</div>
          <div className="font-mono font-semibold">{fmtPct(candidate.approx_pop * 100, 0)}</div>
        </div>
      </div>

      <div className="text-sm">
        <span className="text-xs text-muted-foreground">
          Breakeven{candidate.breakevens.length > 1 ? "s" : ""}:{" "}
        </span>
        <span className="font-mono">{candidate.breakevens.map((b) => fmtNum(b, 1)).join(" / ")}</span>
      </div>
    </div>
  );
}
