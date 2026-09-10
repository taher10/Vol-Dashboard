import type { StrategyCandidate } from "@/lib/api";
import { fmtDate, fmtNum, fmtPct, fmtUsd } from "@/lib/format";
import { COLOR_CALL, COLOR_PUT } from "@/lib/theme";
import { cn } from "@/lib/utils";

/**
 * Structure / legs / net credit-debit / max profit-loss / breakeven / POP
 * card -- shared by Strategy Builder's "Recommended Trade" and Backtest's
 * "Entry Trade", so both render identically instead of drifting apart.
 */
export function TradeCard({ candidate }: { candidate: StrategyCandidate }) {
  // Only the calendar's legs carry their own expiration (they expire on
  // different dates) -- every other structure leaves this null on every leg.
  const isCalendar = candidate.legs.some((leg) => leg.expiration != null);

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <span className="text-lg font-semibold">{candidate.structure}</span>
        {!isCalendar && (
          <span className="text-sm text-muted-foreground">
            {fmtDate(candidate.expiration)} · {candidate.dte}d
          </span>
        )}
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
            {leg.expiration != null && (
              <span className="rounded-sm bg-accent px-1 py-0.5 text-[10px] font-medium uppercase tracking-wide text-accent-foreground">
                {fmtDate(leg.expiration)}
              </span>
            )}
            <span className="ml-auto text-xs text-muted-foreground">
              delta {fmtNum(leg.delta, 2)} · IV {fmtNum(leg.implied_volatility, 1)} · ${fmtNum(leg.mid, 2)}
            </span>
          </div>
        ))}
      </div>

      {isCalendar ? (
        <div className="border-t border-border pt-3 text-sm">
          <div className="flex items-center justify-between">
            <div>
              <div className="text-xs text-muted-foreground">
                {candidate.net_debit_credit >= 0 ? "Net debit" : "Net credit"}
              </div>
              <div className="font-mono font-semibold">{fmtUsd(Math.abs(candidate.net_debit_credit))}</div>
            </div>
            <div className="text-right">
              <div className="text-xs text-muted-foreground">Modeled edge (IV-crush estimate)</div>
              <div
                className={cn(
                  "font-mono font-semibold",
                  candidate.variance_edge != null && candidate.variance_edge.net_vega_pnl >= 0
                    ? "text-[#0b5c0b]"
                    : "text-[#8f2323]"
                )}
              >
                {candidate.variance_edge != null ? fmtUsd(candidate.variance_edge.net_vega_pnl) : "No signal"}
              </div>
            </div>
          </div>
          {candidate.variance_edge != null && (
            <p className="mt-1.5 text-xs text-muted-foreground">
              Front crush {fmtNum(candidate.variance_edge.front_crush, 1)}pt · back crush{" "}
              {fmtNum(candidate.variance_edge.back_crush, 1)}pt vs. an estimated {fmtNum(candidate.variance_edge.iv_ex, 1)}
              % post-event baseline IV — a modeled estimate from real recorded IV + vega, not a guaranteed outcome. Max
              profit/loss aren&apos;t computed for calendars (see the walk-forward P&amp;L below instead).
            </p>
          )}
        </div>
      ) : (
        <>
          <div className="grid grid-cols-2 gap-3 border-t border-border pt-3 text-sm sm:grid-cols-4">
            <div>
              <div className="text-xs text-muted-foreground">
                {candidate.net_debit_credit >= 0 ? "Net debit" : "Net credit"}
              </div>
              <div className="font-mono font-semibold">{fmtUsd(Math.abs(candidate.net_debit_credit))}</div>
            </div>
            <div>
              <div className="text-xs text-muted-foreground">Max profit</div>
              <div className="font-mono font-semibold text-[#0b5c0b]">{fmtUsd(candidate.max_profit)}</div>
            </div>
            <div>
              <div className="text-xs text-muted-foreground">Max loss</div>
              <div className="font-mono font-semibold text-[#8f2323]">{fmtUsd(candidate.max_loss)}</div>
            </div>
            <div>
              <div className="text-xs text-muted-foreground">Approx. POP</div>
              <div className="font-mono font-semibold">
                {candidate.approx_pop != null ? fmtPct(candidate.approx_pop * 100, 0) : "—"}
              </div>
            </div>
          </div>

          <div className="text-sm">
            <span className="text-xs text-muted-foreground">
              Breakeven{candidate.breakevens.length > 1 ? "s" : ""}:{" "}
            </span>
            <span className="font-mono">{candidate.breakevens.map((b) => fmtNum(b, 1)).join(" / ")}</span>
          </div>
        </>
      )}
    </div>
  );
}
