"""
src/metrics.py

VolatilityMetrics — computes vol surface metrics from a flat options chain DataFrame.

Metrics
-------
  term_structure   ATM IV by expiration (vol term structure)
  delta_skew       IV(25Δ put) − IV(25Δ call) per expiration
  skew_ratio       IV(25Δ put) / IV(25Δ call) per expiration
  curvature        Butterfly: (IV25P + IV25C)/2 − IV(ATM)
  realized_vol     Rolling close-to-close historical vol
  vrp              ATM IV − realized vol

Usage
-----
    vm = VolatilityMetrics(chain, price_history=prices)
    all_metrics = vm.compute_all()          # → dict[str, pd.DataFrame]
    skew        = vm.delta_skew()
    ts          = vm.atm_iv_term_structure()
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.interpolate import interp1d


class VolatilityMetrics:
    """Computes volatility surface metrics from an options chain DataFrame."""

    def __init__(
        self,
        chain: pd.DataFrame,
        price_history: pd.DataFrame | None = None,
    ) -> None:
        self.chain = chain
        self.price_history = price_history

    # ------------------------------------------------------------------
    # 1. ATM IV Term Structure
    # ------------------------------------------------------------------

    def atm_iv_term_structure(self, atm_delta: float = 0.50) -> pd.DataFrame:
        """ATM IV for each expiration using nearest observed call delta to `atm_delta`."""
        calls = self.chain[self.chain["optionType"] == "CALL"].copy()
        records = []
        for (expiration, dte), grp in calls.groupby(["expiration", "dte"]):
            iv = self._nearest_iv_at_delta(grp, atm_delta)
            records.append({"expiration": expiration, "dte": dte, "atm_iv": iv})
        return pd.DataFrame(records).sort_values("dte").reset_index(drop=True)

    # ------------------------------------------------------------------
    # 2. 25-Delta Skew
    # ------------------------------------------------------------------

    def delta_skew(self, target_delta: float = 0.25) -> pd.DataFrame:
        """IV(NΔ put) − IV(NΔ call) per expiration. Positive = puts richer."""
        records = []
        pct = int(target_delta * 100)
        for (expiration, dte), grp in self.chain.groupby(["expiration", "dte"]):
            iv_put = self._interpolate_iv_at_delta(grp[grp["optionType"] == "PUT"], target_delta, "PUT")
            iv_call = self._interpolate_iv_at_delta(grp[grp["optionType"] == "CALL"], target_delta, "CALL")
            skew = iv_put - iv_call if not (np.isnan(iv_put) or np.isnan(iv_call)) else float("nan")
            records.append({
                "expiration": expiration, "dte": dte,
                f"iv_{pct}p": iv_put, f"iv_{pct}c": iv_call, "skew": skew,
            })
        return pd.DataFrame(records).sort_values("dte").reset_index(drop=True)

    # ------------------------------------------------------------------
    # 3. Put/Call Skew Ratio
    # ------------------------------------------------------------------

    def skew_ratio(self, target_delta: float = 0.25) -> pd.DataFrame:
        """IV(NΔ put) / IV(NΔ call) per expiration. Ratio > 1 → puts richer."""
        pct = int(target_delta * 100)
        df = self.delta_skew(target_delta).copy()
        df["skew_ratio"] = df[f"iv_{pct}p"] / df[f"iv_{pct}c"]
        df.rename(columns={f"iv_{pct}p": "iv_put", f"iv_{pct}c": "iv_call"}, inplace=True)
        return df[["expiration", "dte", "iv_put", "iv_call", "skew_ratio"]].copy()

    # ------------------------------------------------------------------
    # 4. Smile Curvature (Butterfly / Kurtosis)
    # ------------------------------------------------------------------

    def smile_curvature(self, wing_delta: float = 0.25, atm_delta: float = 0.50) -> pd.DataFrame:
        """
        Butterfly per expiration:
            curvature = (IV(NΔ put) + IV(NΔ call)) / 2  −  IV(ATM)
        Higher value → more pronounced wings (more kurtosis priced in).
        """
        records = []
        for (expiration, dte), grp in self.chain.groupby(["expiration", "dte"]):
            iv_put = self._interpolate_iv_at_delta(grp[grp["optionType"] == "PUT"], wing_delta, "PUT")
            iv_call = self._interpolate_iv_at_delta(grp[grp["optionType"] == "CALL"], wing_delta, "CALL")
            iv_atm = self._nearest_iv_at_delta(grp[grp["optionType"] == "CALL"], atm_delta)
            if any(np.isnan(v) for v in [iv_put, iv_call, iv_atm]):
                wing_avg, curvature = float("nan"), float("nan")
            else:
                wing_avg = (iv_put + iv_call) / 2.0
                curvature = wing_avg - iv_atm
            records.append({
                "expiration": expiration, "dte": dte,
                "iv_wing_avg": wing_avg, "atm_iv": iv_atm, "curvature": curvature,
            })
        return pd.DataFrame(records).sort_values("dte").reset_index(drop=True)

    # ------------------------------------------------------------------
    # 5. Realized Volatility
    # ------------------------------------------------------------------

    def realized_volatility(
        self,
        price_col: str = "close",
        window: int = 21,
        trading_days: int = 252,
    ) -> pd.Series:
        """
        Rolling close-to-close realized vol from self.price_history.
        Returns annualized vol as a percentage (matching Schwab IV scale).
        """
        if self.price_history is None:
            raise ValueError("price_history must be provided to compute realized_volatility.")
        prices = self.price_history[price_col].astype(float)
        log_returns = np.log(prices / prices.shift(1))
        return log_returns.rolling(window).std() * np.sqrt(trading_days) * 100.0

    # ------------------------------------------------------------------
    # 6. VRP
    # ------------------------------------------------------------------

    def vrp(self, window: int = 21, atm_delta: float = 0.50) -> pd.DataFrame:
        """
        VRP per expiration: ATM IV − realized vol (most recent rolling window).
        Positive → options rich; negative → options cheap.
        Requires price_history to be set.
        """
        if self.price_history is None:
            raise ValueError("price_history must be provided to compute VRP.")
        ts_df = self.atm_iv_term_structure(atm_delta=atm_delta)
        rv_series = self.realized_volatility(window=window)
        latest_rv = rv_series.dropna().iloc[-1] if not rv_series.dropna().empty else float("nan")
        ts_df["realized_vol"] = latest_rv
        ts_df["vrp"] = ts_df["atm_iv"] - ts_df["realized_vol"]
        return ts_df[["expiration", "dte", "atm_iv", "realized_vol", "vrp"]].copy()

    # ------------------------------------------------------------------
    # 7. Convenience: compute everything at once
    # ------------------------------------------------------------------

    def compute_all(
        self,
        target_delta: float = 0.25,
        rv_window: int = 21,
    ) -> dict[str, pd.DataFrame]:
        """
        Run all metrics and return a dict keyed by metric name.
        Keys: term_structure, skew, skew_ratio, curvature, vrp (if price_history set).
        """
        results: dict[str, pd.DataFrame] = {
            "term_structure": self.atm_iv_term_structure(),
            "skew":           self.delta_skew(target_delta),
            "skew_ratio":     self.skew_ratio(target_delta),
            "curvature":      self.smile_curvature(wing_delta=target_delta),
        }
        if self.price_history is not None:
            results["vrp"] = self.vrp(window=rv_window)
        return results

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _interpolate_iv_at_delta(
        sub: pd.DataFrame,
        target_delta: float,
        option_type: str,
    ) -> float:
        """Linear interpolation of IV at a target absolute delta. Returns NaN on failure."""
        df = sub.dropna(subset=["delta", "impliedVolatility"]).copy()
        if df.empty:
            return float("nan")
        df["abs_delta"] = df["delta"].abs()
        df = df[(df["abs_delta"] >= 0.01) & (df["abs_delta"] <= 0.99)].sort_values("abs_delta")
        if len(df) < 2:
            return float("nan")
        if target_delta < df["abs_delta"].min() or target_delta > df["abs_delta"].max():
            return float("nan")
        try:
            interp = interp1d(df["abs_delta"].values, df["impliedVolatility"].values,
                              kind="linear", bounds_error=True)
            return float(interp(target_delta))
        except Exception:
            return float("nan")

    @staticmethod
    def _nearest_iv_at_delta(sub: pd.DataFrame, target_delta: float) -> float:
        """
        Return IV at the observed option whose |delta| is nearest to target_delta.
        This avoids interpolation and uses a directly quoted contract.
        """
        df = sub.dropna(subset=["delta", "impliedVolatility"]).copy()
        if df.empty:
            return float("nan")

        df["abs_delta"] = df["delta"].abs()
        df = df[(df["abs_delta"] >= 0.01) & (df["abs_delta"] <= 0.99)]
        if df.empty:
            return float("nan")

        # Pick nearest observed delta to target (stable tie-breaker by strike)
        df["delta_distance"] = (df["abs_delta"] - target_delta).abs()
        row = df.sort_values(["delta_distance", "strikePrice"]).iloc[0]
        return float(row["impliedVolatility"])
