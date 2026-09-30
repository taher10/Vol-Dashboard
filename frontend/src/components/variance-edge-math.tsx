"use client";

import { useState } from "react";
import { ChevronDown, Sigma } from "lucide-react";

import { fmtNum, fmtUsd } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { CalendarEdgeRow } from "@/lib/api";

/** The forward-variance derivation behind the Calendar ranking, written out.
 *
 * "How this works" already said what the number is and how far to trust it,
 * but never showed the algebra -- so the ranking was a black box you were
 * asked to take on faith, which is the opposite of what the rest of this
 * project does. The formula lives in one line of
 * strategy_engine.calendar_variance_edge()'s docstring; a trader deciding
 * whether to put capital behind it should not have to read Python to find
 * it.
 *
 * Substitutes the top-ranked row's real numbers into every step when one is
 * available, because a derivation with live values attached is checkable --
 * you can follow it on a calculator and confirm the page isn't lying. The
 * symbolic form stays visible alongside so it reads as maths rather than as
 * a coincidence of today's data. */

function Frac({ num, den }: { num: React.ReactNode; den: React.ReactNode }) {
  return (
    <span className="inline-flex flex-col items-center align-middle text-[0.95em] leading-tight">
      <span className="px-1.5 pb-0.5">{num}</span>
      <span className="w-full border-t border-current px-1.5 pt-0.5">{den}</span>
    </span>
  );
}

function Step({
  n,
  title,
  children,
}: {
  n: number;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex gap-3">
      <span className="mt-0.5 flex size-5 shrink-0 items-center justify-center rounded-full border border-border text-[10px] font-medium tabular-nums text-muted-foreground">
        {n}
      </span>
      <div className="min-w-0 flex-1">
        <div className="mb-1 text-xs font-medium text-foreground">{title}</div>
        <div className="text-xs leading-relaxed text-muted-foreground">{children}</div>
      </div>
    </div>
  );
}

function Formula({ children }: { children: React.ReactNode }) {
  return (
    <div className="my-2 overflow-x-auto rounded-md border border-border bg-muted/40 px-3 py-2 font-mono text-xs text-foreground">
      {children}
    </div>
  );
}

export function VarianceEdgeMath({ example }: { example?: CalendarEdgeRow | null }) {
  const [open, setOpen] = useState(false);

  const ve = example?.candidate?.variance_edge ?? null;
  const legs = example?.candidate?.legs ?? [];
  const front = legs.find((l) => l.action === "sell") ?? null;
  const back = legs.find((l) => l.action === "buy") ?? null;
  const live = example && ve && front && back ? { row: example, ve, front, back } : null;

  return (
    <div className="mb-4 rounded-lg border border-border bg-card">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="flex w-full items-center gap-2 px-4 py-2.5 text-left transition-colors hover:bg-accent/40"
      >
        <Sigma className="size-3.5 text-muted-foreground" />
        <span className="text-xs font-medium text-foreground">The math</span>
        <span className="truncate text-[11px] text-muted-foreground">
          how the forward-variance edge is derived
          {live ? ` — worked through with ${live.row.symbol}` : ""}
        </span>
        <ChevronDown className={cn("ml-auto size-3.5 shrink-0 text-muted-foreground transition-transform", open && "rotate-180")} />
      </button>

      {open && (
        <div className="flex flex-col gap-4 border-t border-border px-4 py-4">
          <Step n={1} title="Variance adds over time. Volatility does not.">
            An implied vol of <span className="font-mono">σ</span> over <span className="font-mono">T</span> days
            prices total variance <span className="font-mono">σ²·T</span>. That is the quantity you can add and
            subtract across periods — which is why the whole calculation is done in variance and only converted
            back to a vol at the end.
          </Step>

          <Step n={2} title="Subtract the front period out of the back period.">
            The back leg&apos;s variance covers everything up to its own expiry, including the stretch the front
            leg covers. Removing the front leg&apos;s variance leaves only what the market prices for the window{" "}
            <span className="font-mono">between</span> the two expirations:
            <Formula>
              σ<sub>back</sub>²·T<sub>back</sub> − σ<sub>front</sub>²·T<sub>front</sub>
              {live && (
                <span className="ml-2 text-muted-foreground">
                  = {fmtNum(back!.implied_volatility, 1)}²·{live.row.back_dte} −{" "}
                  {fmtNum(front!.implied_volatility, 1)}²·{live.row.front_dte}
                </span>
              )}
            </Formula>
          </Step>

          <Step n={3} title="Convert that leftover variance back into a vol.">
            Divide by the length of the window and take the square root. This is the{" "}
            <span className="text-foreground">ex-event vol</span> — what the market implies for the period{" "}
            <em>after</em> the front expiry, with the front period&apos;s content stripped out:
            <Formula>
              <span className="inline-flex items-center gap-1.5">
                σ<sub>ex</sub> = √
                <Frac
                  num={
                    <>
                      σ<sub>back</sub>²·T<sub>back</sub> − σ<sub>front</sub>²·T<sub>front</sub>
                    </>
                  }
                  den={
                    <>
                      T<sub>back</sub> − T<sub>front</sub>
                    </>
                  }
                />
                {live && <span className="text-muted-foreground">= {fmtNum(live.ve.iv_ex, 2)}</span>}
              </span>
            </Formula>
            If that fraction comes out negative the front leg isn&apos;t actually inflated relative to the back
            leg. There is no edge to measure, so the symbol is dropped from the table rather than shown as zero —
            absence here means &quot;no signal&quot;, not &quot;no data&quot;.
          </Step>

          <Step n={4} title="Assume both legs converge to that vol once the event passes.">
            The edge is the distance each leg still has to fall:
            <Formula>
              <div>
                crush<sub>front</sub> = σ<sub>front</sub> − σ<sub>ex</sub>
                {live && (
                  <span className="ml-2 text-muted-foreground">
                    = {fmtNum(front!.implied_volatility, 1)} − {fmtNum(live.ve.iv_ex, 2)} ={" "}
                    <span className="text-foreground">{fmtNum(live.ve.front_crush, 2)}</span>
                  </span>
                )}
              </div>
              <div className="mt-1">
                crush<sub>back</sub> = σ<sub>back</sub> − σ<sub>ex</sub>
                {live && (
                  <span className="ml-2 text-muted-foreground">
                    = {fmtNum(back!.implied_volatility, 1)} − {fmtNum(live.ve.iv_ex, 2)} ={" "}
                    <span className="text-foreground">{fmtNum(live.ve.back_crush, 2)}</span>
                  </span>
                )}
              </div>
            </Formula>
            The front leg is the one carrying the event, so it normally has further to fall — and you are short
            it.
          </Step>

          <Step n={5} title="Price each crush with the broker's own vega.">
            Vega is dollars per 1 IV point per share; ×100 for the contract. Schwab supplies it per contract, so
            nothing here is modelled — the sign is what encodes the position:
            <Formula>
              <div>
                P&amp;L<sub>front</sub> = <span className="text-pos">+</span>vega<sub>front</sub> · crush
                <sub>front</sub> · 100
                {live && (
                  <span className="ml-2 text-muted-foreground">
                    = <span className="text-foreground">{fmtUsd(live.ve.front_vega_pnl)}</span>
                  </span>
                )}
              </div>
              <div className="mt-1">
                P&amp;L<sub>back</sub> = <span className="text-neg">−</span>vega<sub>back</sub> · crush
                <sub>back</sub> · 100
                {live && (
                  <span className="ml-2 text-muted-foreground">
                    = <span className="text-foreground">{fmtUsd(live.ve.back_vega_pnl)}</span>
                  </span>
                )}
              </div>
              <div className="mt-1.5 border-t border-border pt-1.5">
                net = P&amp;L<sub>front</sub> + P&amp;L<sub>back</sub>
                {live && (
                  <span className="ml-2 text-muted-foreground">
                    = <span className="text-foreground">{fmtUsd(live.ve.net_vega_pnl)}</span>
                  </span>
                )}
              </div>
            </Formula>
            You are short the front leg, so its IV falling is a gain; long the back leg, so its IV falling is a
            loss. The trade works when the first outruns the second.
          </Step>

          <div className="rounded-md border border-border bg-muted/30 px-3 py-2.5">
            <div className="mb-1.5 text-xs font-medium text-foreground">What this assumes</div>
            <ul className="list-disc space-y-1 pl-4 text-xs leading-relaxed text-muted-foreground">
              <li>
                The same <em>absolute</em> event variance sits in both expirations. That is the load-bearing
                assumption, and it is a heuristic — not something the chain can confirm.
              </li>
              <li>
                Vega is locally linear. It is the broker&apos;s vega at today&apos;s spot and IV, so a large move
                in either invalidates it.
              </li>
              <li>
                Pure vol repricing — no delta, gamma, or theta P&amp;L. A calendar that is right about vol and
                wrong about direction can still lose.
              </li>
              <li>
                It is an estimate of structural edge, not a backtested win rate. The Backtest page is where an
                actual trade gets a track record.
              </li>
            </ul>
          </div>
        </div>
      )}
    </div>
  );
}
