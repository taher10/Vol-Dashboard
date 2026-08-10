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


def score_expiries(
    metrics: dict[str, pd.DataFrame], iv_zscore_by_dte: dict[int, dict] | None = None
) -> pd.DataFrame:
    """
    Merge VolatilityMetrics.compute_all() output into one row per expiry with
    decision-ready labels layered on top of the raw numbers.

    - vrp_z: how rich/cheap this expiry's IV is vs its own realized vol,
      standardized across expiries. Needs price history at snapshot time
      (job.py stopped collecting it to save API calls), so this is usually
      NaN for current data -- kept around for symbols/history that do have
      it, and still drives the VRP chart.
    - iv_z: how rich/cheap this expiry's ATM IV is vs *its own trailing
      history at that DTE* (HistoryStore.atm_iv_zscore_by_dte) -- doesn't
      need price history at all, just the daily metric_history accumulation
      the pipeline already does. This is the primary richness signal now
      that vrp_z is usually unavailable; pass `iv_zscore_by_dte` (looked up
      by the caller, since it needs DB access this pure function doesn't
      otherwise have) to enable it.
    - richness_z / richness_basis: richness_z coalesces iv_z (preferred)
      and vrp_z into whichever signal actually produced richness_label;
      richness_basis records which one ("iv_history" / "vrp" / None) so
      callers can phrase commentary correctly ("vs. its own historical IV
      range" vs. "vs. its own realized vol") instead of always claiming the
      realized-vol framing even when that's not what was actually computed.
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

    if iv_zscore_by_dte:
        df["iv_z"] = df["dte"].map(lambda d: iv_zscore_by_dte.get(int(d), {}).get("zscore"))
        df["iv_z"] = pd.to_numeric(df["iv_z"], errors="coerce")
    else:
        df["iv_z"] = np.nan

    df["richness_z"] = df["iv_z"].where(df["iv_z"].notna(), df["vrp_z"])
    df["richness_basis"] = np.select(
        [df["iv_z"].notna(), df["vrp_z"].notna()], ["iv_history", "vrp"], default=None
    )
    df["richness_label"] = _label_from_z(df["richness_z"], "Rich", "Cheap", "Neutral")

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
    statistically notable expiry (highest |richness_z|, i.e. furthest from
    "typical" for this symbol's own curve) and reports its richness and
    skew tilt.

    Deliberately says "vs. its own {basis}" rather than just "richest" --
    richness and absolute IV level are different things (a name can have
    much lower raw IV than another and still show a bigger gap vs. its own
    baseline), and a bare "richest in basket" claim sitting right next to a
    chart where a different symbol is visibly higher reads as wrong even
    though it's answering a different question. Confirmed this was a real
    point of confusion, not just a hypothetical one. The basis itself
    (historical IV range vs. realized vol) varies by row -- richness_basis
    records which one score_expiries() actually used -- since claiming
    "vs. realized vol" when the number was actually computed from trailing
    IV history would be its own kind of wrong.

    basket_ranks, if given, is one comparable number per symbol currently
    selected in the sidebar (each symbol's own most-notable *signed*
    richness_z) -- the cross-symbol clause is only appended when there's
    more than one symbol to actually compare against.
    """
    if expiry_scores is None or expiry_scores.empty:
        return f"{symbol}: no expiry data available yet."

    valid = expiry_scores.dropna(subset=["richness_z"])
    if valid.empty:
        return f"{symbol}: not enough history yet to size up richness for this snapshot."

    notable = valid.loc[valid["richness_z"].abs().idxmax()]
    dte = int(notable["dte"])
    expiration = pd.Timestamp(notable["expiration"]).strftime("%b %d")
    richness_z = float(notable["richness_z"])
    skew_bias = str(notable["skew_bias"]).lower()
    basis = "realized vol" if notable.get("richness_basis") == "vrp" else "historical IV range"

    if abs(richness_z) < _Z_THRESHOLD:
        sentence = f"{symbol}: IV is roughly flat across the curve (max |z|={abs(richness_z):.1f}) — no expiry stands out as notably rich or cheap vs. its own {basis}."
    else:
        richness = str(notable["richness_label"]).lower()
        sentence = (
            f"{symbol} {expiration} ({dte}d) is {richness} vs. its own {basis} "
            f"(z={richness_z:+.1f}), with a {skew_bias} skew tilt."
        )

    if basket_ranks and len(basket_ranks) > 1 and symbol in basket_ranks:
        ordered = sorted(basket_ranks.items(), key=lambda kv: kv[1], reverse=True)
        rank = next(i for i, (sym, _) in enumerate(ordered, start=1) if sym == symbol)
        if rank == 1:
            sentence += f" Widest richness gap in the {len(basket_ranks)}-symbol basket (not the same as highest raw IV)."
        elif rank == len(basket_ranks):
            sentence += f" Narrowest richness gap in the {len(basket_ranks)}-symbol basket."
        else:
            sentence += f" Ranks {rank}/{len(basket_ranks)} for richness in the selected basket."

    return sentence
