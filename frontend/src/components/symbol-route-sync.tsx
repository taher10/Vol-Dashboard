"use client";

import { useEffect, useRef, useState } from "react";
import { usePathname, useRouter } from "next/navigation";

import { primarySymbol, useSettingsStore } from "@/lib/store";
import { NAV } from "@/lib/nav";

const SYMBOL_SCOPED_BASES = NAV.filter((item) => item.symbolScoped).map((item) => item.href);

/**
 * Keeps a symbol-scoped page (Expiry/Strikes/Screener/History) in sync with
 * the sidebar's symbol picker: if the user changes the primary symbol while
 * already on one of these pages, navigate to that same page for the new
 * symbol instead of silently leaving the URL (and all its data) pointed at
 * the old one — confirmed confusing otherwise, since only the nav *links*
 * picked up the new symbol, not the page you were already looking at.
 *
 * The store's `symbols` is persisted to localStorage, which zustand
 * rehydrates asynchronously *after* first paint — that rehydration itself
 * looks identical to "primary changed" and must NOT be treated as a user
 * action (it would otherwise redirect away from whatever URL/symbol the
 * user actually navigated to, on every fresh page load). Only starts
 * comparing once persist.hasHydrated() is true, and takes the just-hydrated
 * value as its baseline rather than whatever the store had before hydration.
 *
 * Mounted once in the root layout so it's active regardless of which page
 * is current. Renders nothing.
 */
export function SymbolRouteSync() {
  const pathname = usePathname();
  const router = useRouter();
  const symbols = useSettingsStore((s) => s.symbols);
  const primary = primarySymbol(symbols);
  const prevPrimary = useRef(primary);
  // `.persist` is a client-only extension the middleware attaches — during
  // Next's server-side prerendering (e.g. the auto-generated /_not-found
  // page) it's undefined, which would otherwise crash the build. Treat that
  // as "hydrated" (a no-op on the server anyway, since effects never run there).
  const [hydrated, setHydrated] = useState(() => useSettingsStore.persist?.hasHydrated() ?? true);

  useEffect(() => {
    if (hydrated) return;
    return useSettingsStore.persist?.onFinishHydration(() => {
      prevPrimary.current = primarySymbol(useSettingsStore.getState().symbols);
      setHydrated(true);
    });
  }, [hydrated]);

  useEffect(() => {
    if (!hydrated) return;
    if (prevPrimary.current === primary) return;
    prevPrimary.current = primary;

    const base = SYMBOL_SCOPED_BASES.find((b) => pathname === b || pathname.startsWith(`${b}/`));
    if (!base) return;

    const currentSymbol = pathname.slice(base.length + 1).split("/")[0];
    if (currentSymbol && currentSymbol.toUpperCase() !== primary) {
      router.push(`${base}/${primary}`);
    }
  }, [primary, pathname, router, hydrated]);

  return null;
}
