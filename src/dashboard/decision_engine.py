"""
src/dashboard/decision_engine.py

Pure pandas/numpy/scipy scoring layer for the options-vol decision-support
dashboard. No Streamlit, no network I/O — every function here takes and
returns DataFrames so it can be unit-tested standalone against the CSVs in
data/raw and data/processed.

score_expiries: "which expiry looks interesting" — term structure + VRP
richness + smile-wing bias, one row per (expiration, dte). Feeds Overview's
richness table and build_takeaway() below. (Contract-level strike selection
now lives entirely in strategy_engine.py's recommend_trade().)
"""

from __future__ import annotations

import numpy as np
import pandas as pd

JOIN_KEYS = ["expiration", "dte"]
_Z_THRESHOLD = 0.5


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


def build_takeaway(
    symbol: str,
    expiry_scores: pd.DataFrame,
    basket_ranks: dict[str, float] | None = None,
) -> str:
    """
    One-sentence synthesis of score_expiries() output -- turns the 4-chart
    Overview grid + richness table into an instant answer instead of
    something the user has to read off manually. Picks the single most
    statistically notable expiry (highest |vrp_z|, i.e. furthest from
    "typical" for this symbol's own curve) and reports its richness and
    skew tilt.

    Deliberately says "vs. its own realized vol" rather than just "richest"
    -- VRP richness and absolute IV level are different things (a name can
    have much lower raw IV than another and still show a bigger gap vs. its
    own realized vol), and a bare "richest in basket" claim sitting right
    next to a chart where a different symbol is visibly higher reads as
    wrong even though it's answering a different question. Confirmed this
    was a real point of confusion, not just a hypothetical one.

    basket_ranks, if given, is one comparable number per symbol currently
    selected in the sidebar (each symbol's own most-notable *signed*
    vrp_z) -- the cross-symbol clause is only appended when there's more
    than one symbol to actually compare against.
    """
    if expiry_scores is None or expiry_scores.empty:
        return f"{symbol}: no expiry data available yet."

    valid = expiry_scores.dropna(subset=["vrp_z"])
    if valid.empty:
        return f"{symbol}: VRP not available for this snapshot (needs price history at save time)."

    notable = valid.loc[valid["vrp_z"].abs().idxmax()]
    dte = int(notable["dte"])
    expiration = pd.Timestamp(notable["expiration"]).strftime("%b %d")
    vrp_z = float(notable["vrp_z"])
    skew_bias = str(notable["skew_bias"]).lower()

    if abs(vrp_z) < _Z_THRESHOLD:
        sentence = f"{symbol}: VRP is roughly flat across the curve (max |z|={abs(vrp_z):.1f}) — no expiry stands out as notably rich or cheap vs. its own realized vol."
    else:
        richness = str(notable["richness_label"]).lower()
        sentence = (
            f"{symbol} {expiration} ({dte}d) is {richness} vs. its own realized vol "
            f"(VRP z={vrp_z:+.1f}), with a {skew_bias} skew tilt."
        )

    if basket_ranks and len(basket_ranks) > 1 and symbol in basket_ranks:
        ordered = sorted(basket_ranks.items(), key=lambda kv: kv[1], reverse=True)
        rank = next(i for i, (sym, _) in enumerate(ordered, start=1) if sym == symbol)
        if rank == 1:
            sentence += f" Widest IV-vs-RV gap in the {len(basket_ranks)}-symbol basket (not the same as highest raw IV)."
        elif rank == len(basket_ranks):
            sentence += f" Narrowest IV-vs-RV gap in the {len(basket_ranks)}-symbol basket."
        else:
            sentence += f" Ranks {rank}/{len(basket_ranks)} for IV-vs-RV richness in the selected basket."

    return sentence
