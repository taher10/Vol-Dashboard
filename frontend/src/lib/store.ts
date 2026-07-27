/**
 * Shared settings store — the web equivalent of src/dashboard/app.py's
 * AppConfig/render_sidebar(), persisted to localStorage so it survives a
 * refresh (Streamlit's st.session_state equivalent, but durable).
 */

import { create } from "zustand";
import { persist } from "zustand/middleware";

export type Intent = "buy" | "sell";
export type OptionTypeFilter = "BOTH" | "CALL" | "PUT";

interface SettingsState {
  symbols: string[];
  intent: Intent;
  targetDelta: number;
  deltaTolerance: number;
  weightValue: number;
  weightDeltaFit: number;
  weightLiquidity: number;
  dteRange: [number, number];
  optionTypes: OptionTypeFilter;
  minVolume: number;
  minOpenInterest: number;
  maxSpreadPct: number;
  /** Bumped after a successful live-data refresh so pages can refetch. Not meaningful on its own. */
  refreshNonce: number;

  setSymbols: (symbols: string[]) => void;
  toggleSymbol: (symbol: string) => void;
  setIntent: (intent: Intent) => void;
  setTargetDelta: (v: number) => void;
  setDeltaTolerance: (v: number) => void;
  setWeights: (v: { value?: number; deltaFit?: number; liquidity?: number }) => void;
  setDteRange: (range: [number, number]) => void;
  setOptionTypes: (v: OptionTypeFilter) => void;
  setMinVolume: (v: number) => void;
  setMinOpenInterest: (v: number) => void;
  setMaxSpreadPct: (v: number) => void;
  bumpRefreshNonce: () => void;
}

export const useSettingsStore = create<SettingsState>()(
  persist(
    (set, get) => ({
      symbols: ["SPX"],
      intent: "buy",
      targetDelta: 0.25,
      deltaTolerance: 0.15,
      weightValue: 0.4,
      weightDeltaFit: 0.3,
      weightLiquidity: 0.3,
      dteRange: [0, 730],
      optionTypes: "BOTH",
      minVolume: 0,
      minOpenInterest: 0,
      maxSpreadPct: 25,
      refreshNonce: 0,

      setSymbols: (symbols) => set({ symbols: symbols.length ? symbols : ["SPX"] }),
      toggleSymbol: (symbol) => {
        const current = get().symbols;
        const next = current.includes(symbol)
          ? current.filter((s) => s !== symbol)
          : [...current, symbol];
        set({ symbols: next.length ? next : ["SPX"] });
      },
      setIntent: (intent) => set({ intent }),
      setTargetDelta: (targetDelta) => set({ targetDelta }),
      setDeltaTolerance: (deltaTolerance) => set({ deltaTolerance }),
      setWeights: ({ value, deltaFit, liquidity }) =>
        set((s) => ({
          weightValue: value ?? s.weightValue,
          weightDeltaFit: deltaFit ?? s.weightDeltaFit,
          weightLiquidity: liquidity ?? s.weightLiquidity,
        })),
      setDteRange: (dteRange) => set({ dteRange }),
      setOptionTypes: (optionTypes) => set({ optionTypes }),
      setMinVolume: (minVolume) => set({ minVolume }),
      setMinOpenInterest: (minOpenInterest) => set({ minOpenInterest }),
      setMaxSpreadPct: (maxSpreadPct) => set({ maxSpreadPct }),
      bumpRefreshNonce: () => set((s) => ({ refreshNonce: s.refreshNonce + 1 })),
    }),
    { name: "vol-dashboard-settings" }
  )
);

export const primarySymbol = (symbols: string[]) => symbols[0] ?? "SPX";
