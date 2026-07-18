"""
src/dashboard/decision_engine.py

Pure pandas/numpy/scipy scoring layer for the options-vol decision-support
dashboard. No Streamlit, no network I/O — every function here takes and
returns DataFrames so it can be unit-tested standalone against the CSVs in
data/raw and data/processed.

Two independent scoring surfaces:
  score_expiries   "which expiry looks interesting" — term structure + VRP
                    richness + smile-wing bias, one row per (expiration, dte).
  score_contracts   "which specific contract to trade" — a 0-100 composite of
                    liquidity, delta fit to a target, and cheap/rich-vs-smile
                    value, one row per contract in the chain.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
import pandas as pd

JOIN_KEYS = ["expiration", "dte"]
GROUP_COLS = ["expiration", "optionType"]
_Z_THRESHOLD = 0.5


@dataclass
class ScoreWeights:
    value: float = 0.4
    delta_fit: float = 0.3
    liquidity: float = 0.3


@dataclass
class ContractFilters:
    dte_range: tuple[int, int] = (0, 730)
    option_types: tuple[str, ...] = ("CALL", "PUT")
    min_volume: int = 0
    min_open_interest: int = 0
    max_spread_pct: float = 25.0


# ----------------------------------------------------------------------
# Expiry-level scoring
# ----------------------------------------------------------------------


def _zscore(s: pd.Series) -> pd.Series:
    """Z-score a series over its non-null values; NaN everywhere if <2 points or zero variance."""
    valid = s.dropna()
    if len(valid) < 2:
        return pd.Series(np.nan, index=s.index)
    std = valid.std()
    if not std or np.isnan(std):
        return pd.Series(np.nan, index=s.index)
    return (s - valid.mean()) / std


def _label_from_z(z: pd.Series, high: str, low: str, mid: str, threshold: float = _Z_THRESHOLD) -> pd.Series:
    """Three-way bucket a z-score series; NaN z-scores fall through to `mid` (no signal, not "neutral fact")."""
    values = np.select([z > threshold, z < -threshold], [high, low], default=mid)
    return pd.Series(values, index=z.index)


def score_expiries(metrics: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """
    Merge VolatilityMetrics.compute_all() output into one row per expiry with
    decision-ready labels layered on top of the raw numbers.

    - vrp_z: how rich/cheap this expiry's IV is vs its own realized vol,
      standardized across expiries so "rich" means rich *relative to the
      other expiries on offer*, not to some fixed constant.
    - richness_label / skew_bias: the z-scores collapsed to a glance-able
      Rich/Cheap/Neutral and Puts-richer/Calls-richer/Balanced tag using the
      same +/-0.5 threshold in both cases (skew_bias uses skew's sign
      directly — positive skew already means "puts richer" per
      VolatilityMetrics.delta_skew).
    - has_wing_data: flags expiries where the 25-delta wings couldn't be
      interpolated (too far out for interp1d's observed-delta-range
      requirement in _interpolate_iv_at_delta) so callers can grey out
      skew/curvature for those rows instead of showing a misleading NaN.
    """
    if "term_structure" not in metrics or metrics["term_structure"] is None:
        raise ValueError("metrics must include a 'term_structure' DataFrame")

    df = metrics["term_structure"][JOIN_KEYS + ["atm_iv"]].copy()

    skew_df = metrics.get("skew")
    if skew_df is not None and not skew_df.empty:
        cols = [c for c in ("iv_25p", "iv_25c", "skew") if c in skew_df.columns]
        df = df.merge(skew_df[JOIN_KEYS + cols], on=JOIN_KEYS, how="left")
    if "skew" not in df.columns:
        df["skew"] = np.nan

    skew_ratio_df = metrics.get("skew_ratio")
    if skew_ratio_df is not None and not skew_ratio_df.empty:
        cols = [c for c in ("skew_ratio",) if c in skew_ratio_df.columns]
        if cols:
            df = df.merge(skew_ratio_df[JOIN_KEYS + cols], on=JOIN_KEYS, how="left")

    curvature_df = metrics.get("curvature")
    if curvature_df is not None and not curvature_df.empty:
        cols = [c for c in ("curvature",) if c in curvature_df.columns]
        if cols:
            df = df.merge(curvature_df[JOIN_KEYS + cols], on=JOIN_KEYS, how="left")
    if "curvature" not in df.columns:
        df["curvature"] = np.nan

    vrp_df = metrics.get("vrp")
    if vrp_df is not None and not vrp_df.empty:
        cols = [c for c in ("realized_vol", "vrp") if c in vrp_df.columns]
        if cols:
            df = df.merge(vrp_df[JOIN_KEYS + cols], on=JOIN_KEYS, how="left")
    if "vrp" not in df.columns:
        df["vrp"] = np.nan

    df["vrp_z"] = _zscore(df["vrp"])
    df["richness_label"] = _label_from_z(df["vrp_z"], "Rich", "Cheap", "Neutral")

    skew_z = _zscore(df["skew"])
    df["skew_bias"] = _label_from_z(skew_z, "Puts richer", "Calls richer", "Balanced")

    df["has_wing_data"] = df["skew"].notna() & df["curvature"].notna()

    return df.sort_values("dte").reset_index(drop=True)


# ----------------------------------------------------------------------
# Contract-level scoring
# ----------------------------------------------------------------------


def _apply_filters(chain: pd.DataFrame, filters: ContractFilters) -> pd.DataFrame:
    """Apply ContractFilters (including the derived mid/spread_pct gate) before any scoring happens."""
    df = chain.copy()

    lo, hi = filters.dte_range
    df = df[(df["dte"] >= lo) & (df["dte"] <= hi)]
    df = df[df["optionType"].isin(filters.option_types)]
    df = df[df["volume"].fillna(0) >= filters.min_volume]
    df = df[df["openInterest"].fillna(0) >= filters.min_open_interest]

    df["mid"] = (df["bid"] + df["ask"]) / 2.0
    df = df[df["mid"] > 0]  # guard: a non-positive mid makes spread_pct undefined/meaningless
    df["spread_pct"] = (df["ask"] - df["bid"]) / df["mid"] * 100.0
    df = df[df["spread_pct"] <= filters.max_spread_pct]

    return df


def _liquidity_scores(df: pd.DataFrame) -> pd.DataFrame:
    """Within-(expiration, optionType) percentile blend: tight spread + high volume + high OI."""
    g = df.groupby(GROUP_COLS)
    spread_pctile = g["spread_pct"].rank(pct=True) * 100
    volume_pctile = g["volume"].rank(pct=True) * 100
    oi_pctile = g["openInterest"].rank(pct=True) * 100
    df["liquidity_score"] = 0.5 * (100 - spread_pctile) + 0.25 * volume_pctile + 0.25 * oi_pctile
    return df


def _delta_fit_scores(df: pd.DataFrame, target_delta: float, delta_tolerance: float) -> pd.DataFrame:
    """Linear falloff from 100 at exactly target_delta to 0 at target_delta +/- delta_tolerance."""
    df["delta_fit_score"] = (
        100 * (1 - (df["delta"].abs() - target_delta).abs() / delta_tolerance)
    ).clip(lower=0)
    return df


def _value_scores(df: pd.DataFrame, intent: Literal["buy", "sell"]) -> pd.DataFrame:
    """
    Fit a degree-2 IV(|delta|) smile per (expiration, optionType) group and rank
    contracts by how far they sit below (buy) / above (sell) that local fit —
    i.e. cheap or rich relative to their own smile's neighbors, not to some
    global average. Groups with <3 valid (delta, IV) points can't support a
    quadratic fit, so value_score is left NaN for the whole group.
    """
    df["value_score"] = np.nan
    for _, idx in df.groupby(GROUP_COLS).groups.items():
        sub = df.loc[idx]
        valid = sub.dropna(subset=["delta", "impliedVolatility"])
        if len(valid) < 3:
            continue
        abs_delta = valid["delta"].abs().to_numpy()
        iv = valid["impliedVolatility"].to_numpy()
        coeffs = np.polyfit(abs_delta, iv, 2)
        fitted = np.polyval(coeffs, abs_delta)
        smile_residual = pd.Series(iv - fitted, index=valid.index)
        rank_input = -smile_residual if intent == "buy" else smile_residual
        df.loc[valid.index, "value_score"] = rank_input.rank(pct=True) * 100
    return df


def _composite_scores(df: pd.DataFrame, weights: ScoreWeights) -> pd.DataFrame:
    """
    Weighted blend of value/delta_fit/liquidity. When value_score is NaN
    (illiquid smile group) we renormalize over delta_fit+liquidity rather than
    letting a missing value_score silently zero the contract out of contention.
    """
    has_value = df["value_score"].notna()
    composite = pd.Series(np.nan, index=df.index, dtype=float)

    composite.loc[has_value] = (
        weights.value * df.loc[has_value, "value_score"]
        + weights.delta_fit * df.loc[has_value, "delta_fit_score"]
        + weights.liquidity * df.loc[has_value, "liquidity_score"]
    )

    fallback_denom = weights.delta_fit + weights.liquidity
    no_value = ~has_value
    if fallback_denom > 0:
        composite.loc[no_value] = (
            weights.delta_fit * df.loc[no_value, "delta_fit_score"]
            + weights.liquidity * df.loc[no_value, "liquidity_score"]
        ) / fallback_denom

    df["composite_score"] = composite
    return df


_SCORED_COLUMNS = [
    "mid", "spread_pct", "liquidity_score", "delta_fit_score",
    "value_score", "composite_score", "rank",
]


def score_contracts(
    chain: pd.DataFrame,
    intent: Literal["buy", "sell"] = "buy",
    target_delta: float = 0.25,
    delta_tolerance: float = 0.15,
    weights: ScoreWeights = ScoreWeights(),
    filters: ContractFilters = ContractFilters(),
) -> pd.DataFrame:
    """
    Filter `chain` by `filters`, then score each surviving contract 0-100 on
    liquidity, fit to `target_delta`, and (when the local smile supports it)
    cheap/rich value for the given `intent`. Returns the filtered+scored rows
    sorted by composite_score descending with a 1-based `rank` column.
    """
    df = _apply_filters(chain, filters)

    if df.empty:
        return df.assign(**{col: pd.Series(dtype="float64") for col in _SCORED_COLUMNS})

    df = _liquidity_scores(df)
    df = _delta_fit_scores(df, target_delta, delta_tolerance)
    df = _value_scores(df, intent)
    df = _composite_scores(df, weights)

    df = df.sort_values("composite_score", ascending=False, na_position="last").reset_index(drop=True)
    df["rank"] = np.arange(1, len(df) + 1)
    return df


def top_candidates(scored: pd.DataFrame, n: int = 10) -> pd.DataFrame:
    """Convenience: the top n rows of an already-scored/sorted DataFrame."""
    return scored.head(n)
