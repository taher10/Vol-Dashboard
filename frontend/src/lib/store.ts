/**
 * Shared settings store — symbols and the shared viewing window (DTE range),
 * persisted to localStorage so they survive a refresh and stay in sync
 * across every page.
 */

import { create } from "zustand";
import { persist } from "zustand/middleware";

interface SettingsState {
  symbols: string[];
  dteRange: [number, number];
  /** Bumped after a successful live-data refresh so pages can refetch. Not meaningful on its own. */
  refreshNonce: number;

  setSymbols: (symbols: string[]) => void;
  toggleSymbol: (symbol: string) => void;
  setPrimary: (symbol: string) => void;
  setDteRange: (range: [number, number]) => void;
  bumpRefreshNonce: () => void;
}

export const useSettingsStore = create<SettingsState>()(
  persist(
    (set, get) => ({
      symbols: ["SPX"],
      // 7-90 DTE by default: the tradeable, liquid window most vol strategies
      // actually operate in. The takeaway/Strategy Builder treat whatever's in
      // this window as one comparable set -- a 730-day default blended weekly
      // premium-selling candidates with LEAPS-dated ones in the same ranked
      // list, which isn't a real choice a trader would weigh against itself.
      // The full 0-730 range is still available by widening the slider.
      dteRange: [7, 90],
      refreshNonce: 0,

      setSymbols: (symbols) => set({ symbols: symbols.length ? symbols : ["SPX"] }),
      toggleSymbol: (symbol) => {
        const current = get().symbols;
        const next = current.includes(symbol)
          ? current.filter((s) => s !== symbol)
          : [...current, symbol];
        set({ symbols: next.length ? next : ["SPX"] });
      },
      setPrimary: (symbol) => {
        const current = get().symbols;
        if (!current.includes(symbol) || current[0] === symbol) return;
        set({ symbols: [symbol, ...current.filter((s) => s !== symbol)] });
      },
      setDteRange: (dteRange) => set({ dteRange }),
      bumpRefreshNonce: () => set((s) => ({ refreshNonce: s.refreshNonce + 1 })),
    }),
    { name: "vol-dashboard-settings" }
  )
);

export const primarySymbol = (symbols: string[]) => symbols[0] ?? "SPX";
