"use client";

import { useEffect, useState } from "react";
import { Info, X } from "lucide-react";

import { api } from "@/lib/api";

/** A standing notice that these ideas are educational.
 *
 * The pages state real strikes and real dollar figures -- "SELL PUT 7500,
 * net credit $2,470, max loss $17,530" -- and the only framing was an 11px
 * footer reading "Personal use". That is adequate for the person who built
 * it and inadequate the moment a link reaches someone who did not, who has
 * no way to know whether these are suggestions or observations.
 *
 * Deliberately one short line. A wall of caveats gets skimmed, and the
 * single thing a visitor needs to carry away is that nobody should trade
 * from this. Data age is already surfaced where it belongs -- the Overview
 * header carries the snapshot date and a "data age" counter -- so it does
 * not need repeating here.
 *
 * Dismissible, remembered per browser -- the owner sees it once rather
 * than losing a strip of screen forever, while a first-time visitor
 * always sees it. Shown only on the public deployment: locally the
 * audience is the author, who does not need telling.
 */
const STORAGE_KEY = "vol-dashboard-disclaimer-dismissed";

export function DisclaimerBanner() {
  const [show, setShow] = useState(false);

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

  if (!show) return null;

  const dismiss = () => {
    try {
      window.localStorage.setItem(STORAGE_KEY, "1");
    } catch {
      // Storage unavailable; it will reappear next load, which is harmless.
    }
    setShow(false);
  };

  return (
    <div className="flex items-start gap-3 border-b border-amber-500/30 bg-amber-500/10 px-4 py-2.5 text-xs text-amber-100 md:px-6">
      <Info className="mt-0.5 size-4 shrink-0 text-amber-400" />
      <p className="flex-1 leading-relaxed">
        <span className="font-semibold">For educational purposes only.</span> These trade ideas are
        illustrative output from a volatility model — not investment advice, and not a
        recommendation to trade.
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
