"use client";

import { useEffect, useState } from "react";
import { Info, X } from "lucide-react";

import { api } from "@/lib/api";

/** A standing notice that this is a personal project, not advice.
 *
 * The pages state real strikes and real dollar figures -- "SELL PUT 7500,
 * net credit $2,470, max loss $17,530" -- and until now the only framing
 * was a 11px footer reading "Personal use". That is adequate for the
 * person who built it and inadequate the moment a link reaches someone who
 * did not, who has no way to know whether these are suggestions or
 * observations.
 *
 * Two things it has to say, because both are easy to assume wrongly:
 * nobody should trade from this, and the numbers are a frozen snapshot
 * rather than live quotes. A visitor months from now would otherwise read
 * October's surface as today's market.
 *
 * Dismissible, remembered per browser -- the owner sees it once rather
 * than losing a strip of screen forever, while a first-time visitor
 * always sees it. Shown only on the public deployment: locally the
 * audience is the author, who does not need telling.
 */
const STORAGE_KEY = "vol-dashboard-disclaimer-dismissed";

export function DisclaimerBanner() {
  const [show, setShow] = useState(false);
  const [asOf, setAsOf] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    api
      .health()
      .then((h) => {
        if (cancelled || !h.demo_mode) return; // local dev: the author knows
        let dismissed = false;
        try {
          dismissed = window.localStorage.getItem(STORAGE_KEY) === "1";
        } catch {
          dismissed = false; // private window or blocked storage -- show it
        }
        if (!dismissed) setShow(true);
      })
      .catch(() => {
        // Health unreachable: show it anyway. Erring toward displaying the
        // caveat is the safe direction when we cannot tell where we are.
        setShow(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!show) return;
    let cancelled = false;
    api
      .overview(["SPX"], 7, 90)
      .then((r) => !cancelled && setAsOf(r.as_of))
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [show]);

  if (!show) return null;

  const dismiss = () => {
    try {
      window.localStorage.setItem(STORAGE_KEY, "1");
    } catch {
      // Storage unavailable; it will reappear next load, which is harmless.
    }
    setShow(false);
  };

  const snapshot = asOf
    ? new Date(asOf).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" })
    : null;

  return (
    <div className="flex items-start gap-3 border-b border-amber-500/30 bg-amber-500/10 px-4 py-2.5 text-xs text-amber-100 md:px-6">
      <Info className="mt-0.5 size-4 shrink-0 text-amber-400" />
      <p className="flex-1 leading-relaxed">
        <span className="font-semibold">Personal project — not investment advice.</span>{" "}
        The strikes and dollar figures here are illustrative output from a volatility model, not
        recommendations, and nothing on this site has been tested against what actually happened.
        The data is a <span className="font-medium">frozen snapshot</span>
        {snapshot ? ` from ${snapshot}` : ""}, not live quotes — prices shown are stale by design.
        Do not trade from this.
      </p>
      <button
        type="button"
        onClick={dismiss}
        aria-label="Dismiss"
        className="shrink-0 rounded p-0.5 text-amber-300/70 transition-colors hover:bg-amber-500/20 hover:text-amber-100"
      >
        <X className="size-4" />
      </button>
    </div>
  );
}
