"""
src/api/routes.py

REST endpoints for the web dashboard. Every endpoint is a thin wrapper
around the existing pipeline/scoring modules (src/dashboard/data_loader.py,
src/dashboard/decision_engine.py, src/dashboard/strategy_engine.py,
src/history_store.py, src/symbols.py) — no scoring, metrics, or strategy
logic is duplicated here, only HTTP plumbing (query-param parsing/validation
and DataFrame -> JSON serialization).

This app is currently run for personal/local use only (see README's "Web
app" section), so /api/refresh triggers a live Schwab pull directly — that
would need to come back out (or move behind auth) before this is ever
exposed to other users, since it's a mutating, credential-backed action.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

import pandas as pd
from fastapi import APIRouter, HTTPException, Query

from src.data_quality import LiveDataUnavailableError
from src.dashboard import backtest_engine, data_loader, decision_engine, insights, strategy_engine
from src.dashboard.data_loader import SnapshotBundle
from src.history_store import HistoryStore
from src.schwab_database import SchwabDatabase
from src.symbols import SYMBOL_REGISTRY

from .schemas import SymbolOut
from .serialize import clean_value, df_records

router = APIRouter(prefix="/api")

_METRIC_NAMES = ("term_structure", "skew", "skew_ratio", "curvature", "vrp")


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


_history_store = HistoryStore()


def _iv_zscore_lookup(symbol: str, metrics: dict[str, pd.DataFrame]) -> dict[int, dict]:
    """Trailing IV z-score per dte currently on the chain, for score_expiries()'s
    richness signal -- computed here (not inside score_expiries itself) since it
    needs DB access that function deliberately doesn't have."""
    ts = metrics.get("term_structure")
    if ts is None or ts.empty or "dte" not in ts.columns:
        return {}
    dtes = [int(d) for d in ts["dte"].dropna().unique()]
    return _history_store.atm_iv_zscore_by_dte(symbol, dtes)


def _load_bundle(symbol: str) -> SnapshotBundle:
    if symbol not in SYMBOL_REGISTRY:
        raise HTTPException(status_code=404, detail=f"Unknown symbol '{symbol}'.")
    try:
        return data_loader.load_latest_snapshot(symbol)
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail=f"No saved snapshot for '{symbol}' yet. {exc}",
        ) from exc


def _filter_metrics_by_dte(
    metrics: dict[str, pd.DataFrame], dte_min: int, dte_max: int
) -> dict[str, pd.DataFrame]:
    """Filters each metrics DataFrame down to a DTE window before scoring/display."""
    filtered: dict[str, pd.DataFrame] = {}
    for name, df in metrics.items():
        if df is not None and not df.empty and "dte" in df.columns:
            filtered[name] = df[(df["dte"] >= dte_min) & (df["dte"] <= dte_max)]
        else:
            filtered[name] = df
    return filtered


def _parse_date(value: str, label: str = "date") -> "pd.Timestamp":
    """Parse a user-supplied date string, converting an unparseable value
    into a 400 instead of letting pandas' DateParseError bubble up as an
    unhandled 500."""
    try:
        return pd.Timestamp(value)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=f"Invalid {label} '{value}'.") from exc


def _underlying_price(chain: pd.DataFrame | None) -> float | None:
    """Most recent underlyingPrice quoted anywhere in the chain (same value
    repeated per-contract, so any non-null row works) -- lets the UI show a
    trader the actual spot/underlying price, which nothing currently
    surfaces despite it being on every row of the chain already."""
    if chain is None or chain.empty or "underlyingPrice" not in chain.columns:
        return None
    values = chain["underlyingPrice"].dropna()
    return float(values.iloc[0]) if not values.empty else None


def _realized_vol(metrics: dict[str, pd.DataFrame]) -> float | None:
    """This symbol's trailing realized vol (VolatilityMetrics.vrp()'s single
    rolling-window number, broadcast to every row of that table -- same
    "any non-null row works" shape as _underlying_price above). Read
    straight from the metrics bundle rather than through
    decision_engine.score_expiries()'s exact-(expiration,dte) merge: vrp's
    source table isn't recomputed daily (needs price history, which job.py
    stopped collecting to save API calls), so its dte values drift stale
    against the freshly-computed term_structure table and the merge silently
    produces no match -- realized_vol isn't actually per-expiry data anyway,
    so there's nothing lost by reading it directly instead of joining it."""
    vrp_df = metrics.get("vrp")
    if vrp_df is None or vrp_df.empty or "realized_vol" not in vrp_df.columns:
        return None
    values = vrp_df["realized_vol"].dropna()
    return float(values.iloc[0]) if not values.empty else None


def _live_dte(expiration) -> int:
    """Days from today's REAL wall-clock date to `expiration` -- deliberately
    not the `dte` column baked into the chain at fetch time, which is frozen
    at whatever "today" was when that snapshot was pulled and goes stale (or
    even negative, i.e. already expired) the moment the pipeline falls more
    than a day behind. Used anywhere a user is picking a LIVE/current
    expiration (PCR chart, Strike Profile, Calendar Edge leaderboard) --
    NOT by _expirations_list()/backtest, where dte-relative-to-the-
    historical-entry-date is exactly what's wanted, not today's date."""
    return (pd.Timestamp(expiration).date() - date.today()).days


def _expirations_list(chain: pd.DataFrame) -> list[dict]:
    if chain is None or chain.empty or "expiration" not in chain.columns:
        return []
    exp = (
        chain[["expiration", "dte"]]
        .dropna()
        .drop_duplicates()
        .sort_values("dte")
        .reset_index(drop=True)
    )
    return df_records(exp)


# ---------------------------------------------------------------------------
# Symbols
# ---------------------------------------------------------------------------


@router.get("/symbols", response_model=list[SymbolOut])
def list_symbols() -> list[SymbolOut]:
    return [SymbolOut(symbol=sym, color=info.color) for sym, info in SYMBOL_REGISTRY.items()]


# ---------------------------------------------------------------------------
# Scanner — one row per registry symbol, moontower-style sortable table.
# Unlike /api/overview (scoped to user-selected symbols), this loops over
# every symbol in SYMBOL_REGISTRY unconditionally: ~20 symbols x (1 disk
# read + up to 3 SQLite queries each opening/closing their own connection,
# no pooling) per request. Real but small at this scale (same N-symbol-loop
# pattern /api/overview's basket_ranks computation already uses) -- not
# worth a batching layer for an on-demand, unpolled, ~20-row endpoint. The
# first cheap fix if this ever matters is a short server-side cache (data
# only changes once/day), not a DB-layer rewrite.
# ---------------------------------------------------------------------------


def _scanner_row(symbol: str, target_dte: int) -> dict:
    """One representative-expiry row for `symbol` -- the closest-to-
    target_dte row from score_expiries(), so every field in the row shares
    one DTE (consistent with iv_rank/atm_iv_zscore_by_dte's own constant-DTE
    convention, rather than each column picking its own independently-
    relevant expiry). Never raises for a missing/thin-history symbol --
    returns a row with nulls in the history-dependent fields instead, since
    a scanner request must never fail because one of 20 symbols has no
    snapshot yet (same "report gaps, don't hide them" spirit as /api/
    overview's missing_symbols list)."""
    info = SYMBOL_REGISTRY[symbol]
    try:
        bundle = data_loader.load_latest_snapshot(symbol)
    except FileNotFoundError:
        return {
            "symbol": symbol,
            "color": info.color,
            "underlying_price": None,
            "as_of": None,
            "dte": None,
            "expiration": None,
            "atm_iv": None,
            "iv_25p": None,
            "iv_25c": None,
            "skew": None,
            "skew_bias": None,
            "has_wing_data": False,
            "curvature": None,
            "realized_vol": None,
            "richness_z": None,
            "richness_label": None,
            "richness_basis": None,
            "iv_rank": None,
            "iv_percentile": None,
            "days_of_history": 0,
        }

    try:
        expiry_scores = decision_engine.score_expiries(bundle.metrics, _iv_zscore_lookup(symbol, bundle.metrics))
    except ValueError:
        expiry_scores = None

    row = None
    if expiry_scores is not None and not expiry_scores.empty:
        idx = (expiry_scores["dte"] - target_dte).abs().idxmin()
        row = expiry_scores.loc[idx]

    ivr = _history_store.iv_rank(symbol, target_dte=target_dte)
    # Deliberately HistoryStore.snapshot_dates(), not SchwabDatabase.
    # options_snapshot_dates() -- iv_rank/richness_z are computed from
    # HistoryStore's metric_history table, which can accumulate a different
    # number of days than the raw options table (confirmed in practice: two
    # symbols can show the same raw-snapshot count while one has an extra
    # metric_history row from an earlier backfill). Showing the raw-table
    # count here would silently misexplain why iv_rank/richness_z are null
    # for one symbol but not another with the same displayed "History" value.
    days_of_history = len(_history_store.snapshot_dates(symbol))

    return {
        "symbol": symbol,
        "color": info.color,
        "underlying_price": _underlying_price(bundle.chain),
        "as_of": bundle.as_of.isoformat(),
        "dte": clean_value(row["dte"]) if row is not None else None,
        "expiration": clean_value(row["expiration"]) if row is not None else None,
        "atm_iv": clean_value(row["atm_iv"]) if row is not None else None,
        "iv_25p": clean_value(row.get("iv_25p")) if row is not None else None,
        "iv_25c": clean_value(row.get("iv_25c")) if row is not None else None,
        "skew": clean_value(row["skew"]) if row is not None else None,
        "skew_bias": clean_value(row["skew_bias"]) if row is not None else None,
        "has_wing_data": bool(row["has_wing_data"]) if row is not None else False,
        "curvature": clean_value(row["curvature"]) if row is not None else None,
        "realized_vol": _realized_vol(bundle.metrics),
        "richness_z": clean_value(row["richness_z"]) if row is not None else None,
        "richness_label": clean_value(row["richness_label"]) if row is not None else None,
        "richness_basis": clean_value(row["richness_basis"]) if row is not None else None,
        "iv_rank": clean_value(ivr["iv_rank"]) if ivr else None,
        "iv_percentile": clean_value(ivr["iv_percentile"]) if ivr else None,
        "days_of_history": days_of_history,
    }


@router.get("/scanner")
def scanner(target_dte: int = Query(30, ge=0, le=3650)) -> dict:
    return {"target_dte": target_dte, "rows": [_scanner_row(sym, target_dte) for sym in SYMBOL_REGISTRY]}


def _pcr_snapshot(expiration: str | None) -> dict:
    """Put/Call open-interest ratio (total put OI / total call OI, summed
    across every strike at one expiration) for every symbol that literally
    lists `expiration` (a YYYY-MM-DD date string) in its own chain, for the
    Vol Scanner's PCR chart. Deliberately sums the WHOLE chain at that
    expiration, not just the 25-delta wings -- PCR is a positioning/sentiment
    read (who's holding what), not a smile-shape read, so it wants every
    strike's open interest, not just the wing points build_vertical() itself
    trades off of. A symbol with no open interest at all on the call side
    (can't form a ratio) is simply absent from `rows`, matching the same
    "calendar/data mismatch, not a signal" convention _wing_iv_snapshot used
    to follow for this same chart's expiration selector."""
    all_expirations: set[str] = set()
    per_symbol_chain: dict[str, pd.DataFrame] = {}
    for symbol in SYMBOL_REGISTRY:
        try:
            bundle = data_loader.load_latest_snapshot(symbol)
        except FileNotFoundError:
            continue
        chain = bundle.chain
        if chain is None or chain.empty:
            continue
        chain = chain.dropna(subset=["expiration", "dte", "optionType", "openInterest"]).copy()
        if chain.empty:
            continue
        chain["expiration_str"] = chain["expiration"].apply(lambda e: pd.Timestamp(e).date().isoformat())
        per_symbol_chain[symbol] = chain
        all_expirations.update(chain["expiration_str"].unique())

    # dte computed live against today, not the stale per-symbol dte column
    # (see _live_dte()) -- and an expiration already behind today's real
    # date is dropped outright rather than offered as a pickable "current"
    # expiration.
    available_expirations = [
        {"expiration": exp, "dte": dte}
        for exp in sorted(all_expirations)
        if (dte := _live_dte(exp)) >= 0
    ]

    if expiration is None and available_expirations:
        expiration = min(available_expirations, key=lambda e: abs(e["dte"] - 30))["expiration"]

    rows: list[dict] = []
    if expiration:
        for symbol, chain in per_symbol_chain.items():
            exp_chain = chain[chain["expiration_str"] == expiration]
            if exp_chain.empty:
                continue
            put_oi = float(exp_chain.loc[exp_chain["optionType"] == "PUT", "openInterest"].sum())
            call_oi = float(exp_chain.loc[exp_chain["optionType"] == "CALL", "openInterest"].sum())
            if call_oi <= 0:
                continue
            rows.append({
                "symbol": symbol,
                "color": SYMBOL_REGISTRY[symbol].color,
                "put_oi": int(put_oi),
                "call_oi": int(call_oi),
                "pcr": clean_value(put_oi / call_oi),
            })

    return {"expiration": expiration, "available_expirations": available_expirations, "rows": rows}


@router.get("/scanner/pcr")
def scanner_pcr(expiration: str | None = Query(None)) -> dict:
    return _pcr_snapshot(expiration)


def _calendar_edge_row(symbol: str, front_dte: int, back_dte: int, target_delta: float) -> dict | None:
    """One symbol's calendar-edge estimate for the Vol Scanner's leaderboard
    -- picks this symbol's own nearest-available expirations to
    front_dte/back_dte, then reuses strategy_engine.build_calendar_call()
    directly (same picking logic a real calendar backtest entry would use,
    zero duplication) to get its variance_edge. Returns None (skipped by the
    caller) for a symbol with no saved snapshot yet, fewer than two distinct
    expirations, or where build_calendar_call() finds no signal -- never
    raises, same "report gaps, don't hide them" spirit as _scanner_row()."""
    try:
        bundle = data_loader.load_latest_snapshot(symbol)
    except FileNotFoundError:
        return None

    chain = bundle.chain
    if chain is None or chain.empty or "expiration" not in chain.columns:
        return None

    # dte computed live against today (see _live_dte()), not the stale
    # per-contract dte column -- otherwise a stale snapshot could pick an
    # already-expired contract as the "front" or "back" leg.
    dte_table = chain[["expiration"]].dropna().drop_duplicates()
    dte_table["dte"] = dte_table["expiration"].apply(_live_dte)
    dte_table = dte_table[dte_table["dte"] >= 0].sort_values("dte")
    if dte_table.empty:
        return None
    front_idx = (dte_table["dte"] - front_dte).abs().idxmin()
    back_idx = (dte_table["dte"] - back_dte).abs().idxmin()
    front_expiration = dte_table.loc[front_idx, "expiration"]
    back_expiration = dte_table.loc[back_idx, "expiration"]
    if front_expiration == back_expiration:
        return None

    candidate = strategy_engine.build_calendar_call(chain, front_expiration, back_expiration, target_delta)
    if candidate is None or candidate.variance_edge is None:
        return None

    net_debit_credit = candidate.net_debit_credit
    # candidate.max_loss is already a genuine worst-case UPPER BOUND (see
    # build_calendar_call()'s docstring) -- NOT "≈ net debit" the way an
    # earlier version of this endpoint assumed, which was confirmed WRONG
    # (not just imprecise) against a real OptionStrat quote once front/back
    # strikes differ meaningfully. True max profit still has no honest
    # simple number (open-ended, depends on where the stock lands and what
    # IV does when the front leg expires) -- deliberately not included
    # here, same reasoning as Candidate.max_profit=None.
    est_max_loss = candidate.max_loss
    edge_per_capital_pct = (
        clean_value(candidate.variance_edge["net_vega_pnl"] / est_max_loss * 100)
        if est_max_loss
        else None
    )

    return {
        "symbol": symbol,
        "color": SYMBOL_REGISTRY[symbol].color,
        "front_expiration": pd.Timestamp(front_expiration).date().isoformat(),
        "back_expiration": pd.Timestamp(back_expiration).date().isoformat(),
        "front_dte": int(dte_table.loc[front_idx, "dte"]),
        "back_dte": int(dte_table.loc[back_idx, "dte"]),
        "net_debit_credit": clean_value(net_debit_credit),
        "est_max_loss": clean_value(est_max_loss),
        "net_vega_pnl": clean_value(candidate.variance_edge["net_vega_pnl"]),
        "edge_per_capital_pct": edge_per_capital_pct,
    }


@router.get("/scanner/calendar-edge")
def scanner_calendar_edge(
    front_dte: int = Query(7, ge=0, le=3650),
    back_dte: int = Query(30, ge=0, le=3650),
    target_delta: float = Query(0.25, gt=0.0, lt=0.5),
) -> dict:
    rows = [
        row
        for symbol in SYMBOL_REGISTRY
        if (row := _calendar_edge_row(symbol, front_dte, back_dte, target_delta)) is not None
    ]
    return {"front_dte": front_dte, "back_dte": back_dte, "target_delta": target_delta, "rows": rows}


def _delta_neutral_row(symbol: str, target_dte: int, target_delta: float) -> dict | None:
    """One symbol's straddle/strangle screen -- reuses the SAME richness_z
    signal already computed for the Vol Scanner table (decision_engine.
    score_expiries(), same as _scanner_row()) to decide direction: rich
    (richness_z > 0) -> sell candidate (collect the rich premium), cheap
    (richness_z < 0) -> buy candidate (pay the cheap premium for a big-move
    bet). Builds the actual structure via build_straddle_strangle() at that
    same expiry -- real strikes/premiums, not just the ranking number.
    Returns None (skipped by the caller) for a symbol with no snapshot yet,
    no richness signal at this DTE, or where the chain can't support a
    call+put near target_delta -- never raises."""
    try:
        bundle = data_loader.load_latest_snapshot(symbol)
    except FileNotFoundError:
        return None

    try:
        expiry_scores = decision_engine.score_expiries(bundle.metrics, _iv_zscore_lookup(symbol, bundle.metrics))
    except ValueError:
        return None
    if expiry_scores.empty:
        return None

    idx = (expiry_scores["dte"] - target_dte).abs().idxmin()
    row = expiry_scores.loc[idx]
    richness_z = row["richness_z"]
    if pd.isna(richness_z):
        return None

    action: strategy_engine.Action = "sell" if richness_z > 0 else "buy"
    candidate = strategy_engine.build_straddle_strangle(bundle.chain, row["expiration"], action, target_delta)
    if candidate is None:
        return None

    return {
        "symbol": symbol,
        "color": SYMBOL_REGISTRY[symbol].color,
        "expiration": pd.Timestamp(row["expiration"]).date().isoformat(),
        "dte": int(row["dte"]),
        "richness_z": clean_value(richness_z),
        "richness_label": clean_value(row["richness_label"]),
        "action": action,
        "candidate": _candidate_record(candidate),
    }


@router.get("/scanner/delta-neutral")
def scanner_delta_neutral(
    target_dte: int = Query(30, ge=0, le=3650),
    target_delta: float = Query(0.50, gt=0.0, le=0.50),
) -> dict:
    rows = [
        row
        for symbol in SYMBOL_REGISTRY
        if (row := _delta_neutral_row(symbol, target_dte, target_delta)) is not None
    ]
    return {"target_dte": target_dte, "target_delta": target_delta, "rows": rows}


def _term_structure_row(symbol: str, near_dte: int, far_dte: int) -> dict | None:
    """One symbol's term-structure and skew-term-structure slope -- the
    near/far ATM IV and skew read straight off metrics already computed by
    VolatilityMetrics.atm_iv_term_structure()/delta_skew() (same tables
    /api/overview serves), no new metric needed. iv_slope = far IV - near
    IV (positive = normal/contango, negative = inverted); skew_slope =
    far skew - near skew (does downside skew get more or less pronounced
    further out). Returns None (skipped by the caller) for a symbol with no
    snapshot yet or fewer than two usable DTE points in either table --
    never raises, same "report gaps, don't hide them" spirit as
    _scanner_row()."""
    try:
        bundle = data_loader.load_latest_snapshot(symbol)
    except FileNotFoundError:
        return None

    ts = bundle.metrics.get("term_structure")
    skew_df = bundle.metrics.get("skew")
    if ts is None or ts.empty or skew_df is None or skew_df.empty:
        return None

    ts = ts.dropna(subset=["dte", "atm_iv"])
    skew_df = skew_df.dropna(subset=["dte", "skew"])
    if len(ts) < 2 or len(skew_df) < 2:
        return None

    near_iv_idx = (ts["dte"] - near_dte).abs().idxmin()
    far_iv_idx = (ts["dte"] - far_dte).abs().idxmin()
    near_skew_idx = (skew_df["dte"] - near_dte).abs().idxmin()
    far_skew_idx = (skew_df["dte"] - far_dte).abs().idxmin()
    if near_iv_idx == far_iv_idx or near_skew_idx == far_skew_idx:
        return None  # not enough distinct expirations to form a slope

    near_iv = float(ts.loc[near_iv_idx, "atm_iv"])
    far_iv = float(ts.loc[far_iv_idx, "atm_iv"])
    near_skew = float(skew_df.loc[near_skew_idx, "skew"])
    far_skew = float(skew_df.loc[far_skew_idx, "skew"])

    return {
        "symbol": symbol,
        "color": SYMBOL_REGISTRY[symbol].color,
        "near_dte": int(ts.loc[near_iv_idx, "dte"]),
        "far_dte": int(ts.loc[far_iv_idx, "dte"]),
        "near_iv": clean_value(near_iv),
        "far_iv": clean_value(far_iv),
        "iv_slope": clean_value(far_iv - near_iv),
        "near_skew": clean_value(near_skew),
        "far_skew": clean_value(far_skew),
        "skew_slope": clean_value(far_skew - near_skew),
    }


@router.get("/scanner/term-structure")
def scanner_term_structure(
    near_dte: int = Query(7, ge=0, le=3650),
    far_dte: int = Query(60, ge=0, le=3650),
) -> dict:
    rows = [
        row
        for symbol in SYMBOL_REGISTRY
        if (row := _term_structure_row(symbol, near_dte, far_dte)) is not None
    ]
    return {"near_dte": near_dte, "far_dte": far_dte, "rows": rows}


def _strike_profile_snapshot(symbol: str, expiration: str | None) -> dict:
    """Full per-strike IV/delta/gamma/OI curve for one symbol+expiration --
    unlike every other Vol Scanner chart, this deliberately reads the raw
    chain directly (not decision_engine.score_expiries()'s per-expiration
    summary), since delta/gamma/OI only exist at the individual-contract
    level and were never meant to be summarized into one number per expiry.
    Calls and puts are matched by strike into one row each so a caller can
    plot both sides against a single shared x-axis."""
    empty = {
        "symbol": symbol,
        "expiration": None,
        "available_expirations": [],
        "underlying_price": None,
        "strikes": [],
    }
    try:
        bundle = data_loader.load_latest_snapshot(symbol)
    except FileNotFoundError:
        return empty
    chain = bundle.chain
    if chain is None or chain.empty:
        return empty

    # dte computed live against today (see _live_dte()), not the stale
    # per-contract dte column -- and an expiration already behind today's
    # real date is dropped rather than offered as a pickable "current" one.
    available_expirations = sorted(
        (
            {"expiration": exp_str, "dte": dte}
            for exp_str in chain["expiration"].dropna().apply(lambda e: pd.Timestamp(e).date().isoformat()).unique()
            if (dte := _live_dte(exp_str)) >= 0
        ),
        key=lambda e: e["expiration"],
    )
    if not available_expirations:
        return empty

    if expiration is None:
        expiration = min(available_expirations, key=lambda e: abs(e["dte"] - 30))["expiration"]

    chain = chain.copy()
    chain["expiration_str"] = chain["expiration"].apply(lambda e: pd.Timestamp(e).date().isoformat())
    exp_chain = chain[chain["expiration_str"] == expiration]

    call_by_strike: dict[float, pd.Series] = {}
    put_by_strike: dict[float, pd.Series] = {}
    for _, row in exp_chain.iterrows():
        strike = row.get("strikePrice")
        if strike is None or pd.isna(strike):
            continue
        target = call_by_strike if row.get("optionType") == "CALL" else put_by_strike
        target[float(strike)] = row

    strikes = []
    for strike in sorted(set(call_by_strike) | set(put_by_strike)):
        c = call_by_strike.get(strike)
        p = put_by_strike.get(strike)
        strikes.append({
            "strike": strike,
            "call_iv": clean_value(c["impliedVolatility"]) if c is not None else None,
            "put_iv": clean_value(p["impliedVolatility"]) if p is not None else None,
            "call_delta": clean_value(c["delta"]) if c is not None else None,
            "put_delta": clean_value(p["delta"]) if p is not None else None,
            "call_gamma": clean_value(c["gamma"]) if c is not None else None,
            "put_gamma": clean_value(p["gamma"]) if p is not None else None,
            "call_oi": clean_value(c["openInterest"]) if c is not None else None,
            "put_oi": clean_value(p["openInterest"]) if p is not None else None,
        })

    return {
        "symbol": symbol,
        "expiration": expiration,
        "available_expirations": available_expirations,
        "underlying_price": _underlying_price(bundle.chain),
        "strikes": strikes,
    }


@router.get("/scanner/strike-profile")
def scanner_strike_profile(
    symbol: str = Query(...),
    expiration: str | None = Query(None),
) -> dict:
    symbol = symbol.upper()
    if symbol not in SYMBOL_REGISTRY:
        raise HTTPException(status_code=404, detail=f"Unknown symbol '{symbol}'.")
    return _strike_profile_snapshot(symbol, expiration)


# ---------------------------------------------------------------------------
# Overview
# ---------------------------------------------------------------------------


@router.get("/overview")
def overview(
    symbols: str = Query("SPX", description="Comma-separated symbols, e.g. 'SPX,AAPL'"),
    dte_min: int = Query(0, ge=0),
    dte_max: int = Query(730, ge=0),
) -> dict:
    requested = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    if not requested:
        requested = ["SPX"]

    bundles: dict[str, SnapshotBundle] = {}
    missing: list[str] = []
    for sym in requested:
        if sym not in SYMBOL_REGISTRY:
            missing.append(sym)
            continue
        try:
            bundles[sym] = data_loader.load_latest_snapshot(sym)
        except FileNotFoundError:
            missing.append(sym)

    if not bundles:
        raise HTTPException(status_code=404, detail=f"No saved snapshots for any of: {requested}")

    filtered_metrics = {
        sym: _filter_metrics_by_dte(bundle.metrics, dte_min, dte_max) for sym, bundle in bundles.items()
    }

    primary = requested[0] if requested[0] in bundles else next(iter(bundles))

    payload_symbols: dict[str, dict] = {}
    for sym, metrics in filtered_metrics.items():
        payload_symbols[sym] = {
            "color": SYMBOL_REGISTRY[sym].color,
            "underlying_price": _underlying_price(bundles[sym].chain),
            "term_structure": df_records(metrics.get("term_structure")),
            "skew": df_records(metrics.get("skew")),
            "curvature": df_records(metrics.get("curvature")),
            "vrp": df_records(metrics.get("vrp")) if "vrp" in metrics else None,
        }

    expiry_scores = None
    if primary in filtered_metrics and "term_structure" in filtered_metrics[primary]:
        try:
            expiry_scores = decision_engine.score_expiries(
                filtered_metrics[primary], _iv_zscore_lookup(primary, filtered_metrics[primary])
            )
        except ValueError:
            expiry_scores = None

    basket_ranks: dict[str, float] = {}
    if len(bundles) > 1:
        for sym, metrics in filtered_metrics.items():
            try:
                scores = decision_engine.score_expiries(metrics, _iv_zscore_lookup(sym, metrics))
            except ValueError:
                continue
            valid = scores.dropna(subset=["richness_z"])
            if not valid.empty:
                basket_ranks[sym] = float(valid.loc[valid["richness_z"].abs().idxmax(), "richness_z"])

    takeaway = None
    if expiry_scores is not None:
        takeaway = decision_engine.build_takeaway(primary, expiry_scores, basket_ranks)

    commentary = None
    if expiry_scores is not None and not expiry_scores.empty:
        # Prefer the most richness-notable expiry (same selection build_takeaway
        # uses). Falls back to the most skewed expiry with real wing data when no
        # richness signal is available at all (neither trailing IV history nor
        # VRP) -- skew comes straight off the chain's own delta/IV surface, so
        # it's always there even when richness isn't.
        richness_valid = expiry_scores.dropna(subset=["richness_z"])
        if not richness_valid.empty:
            notable_row = expiry_scores.loc[richness_valid["richness_z"].abs().idxmax()]
        else:
            skew_valid = expiry_scores[expiry_scores["has_wing_data"]].dropna(subset=["skew"])
            notable_row = expiry_scores.loc[skew_valid["skew"].abs().idxmax()] if not skew_valid.empty else None
        if notable_row is not None:
            commentary = _commentary_record(insights.expiry_commentary(bundles[primary].chain, notable_row))

    return {
        "primary": primary,
        "as_of": bundles[primary].as_of.isoformat(),
        "requested_symbols": requested,
        "missing_symbols": missing,
        "symbols": payload_symbols,
        "expiry_scores": df_records(expiry_scores) if expiry_scores is not None else [],
        "takeaway": takeaway,
        "commentary": commentary,
    }


# ---------------------------------------------------------------------------
# Strategy Builder — one recommended vertical spread for a stated view
# ---------------------------------------------------------------------------


def _leg_record(leg: strategy_engine.Leg) -> dict:
    return {
        "action": leg.action,
        "optionType": leg.optionType,
        "strike": clean_value(leg.strike),
        "delta": clean_value(leg.delta),
        "mid": clean_value(leg.mid),
        # Only set for calendar_call's legs -- None for every other
        # structure, which shares one expiration at the Candidate level.
        "expiration": pd.Timestamp(leg.expiration).date().isoformat() if leg.expiration is not None else None,
        "implied_volatility": clean_value(leg.implied_volatility),
        "vega": clean_value(leg.vega),
    }


def _candidate_record(c: strategy_engine.Candidate) -> dict:
    # Every numeric field routed through clean_value() -- same convention as
    # df_records() elsewhere in this module -- so a NaN/Infinity anywhere in
    # here (e.g. a future field addition, or a candidate built from unusual
    # chain data) becomes a clean JSON null instead of an unhandled 500 from
    # Starlette's allow_nan=False JSON encoder.
    return {
        "structure": c.structure,
        "direction": c.direction,
        "expiration": pd.Timestamp(c.expiration).date().isoformat(),
        "dte": c.dte,
        "legs": [_leg_record(leg) for leg in c.legs],
        "net_debit_credit": clean_value(c.net_debit_credit),
        "max_profit": clean_value(c.max_profit),
        "max_loss": clean_value(c.max_loss),
        "breakevens": [clean_value(b) for b in c.breakevens],
        "approx_pop": clean_value(c.approx_pop),
        "payoff": [{"underlying": clean_value(p["underlying"]), "pnl": clean_value(p["pnl"])} for p in c.payoff],
        "variance_edge": (
            {k: clean_value(v) for k, v in c.variance_edge.items()} if c.variance_edge is not None else None
        ),
    }


def _commentary_record(c: insights.Commentary) -> dict:
    return {
        "headline": c.headline,
        "interpretation": c.interpretation,
        "trade_angle": c.trade_angle,
        "example_trade": _candidate_record(c.example_trade) if c.example_trade is not None else None,
    }


def _sizing_record(s: strategy_engine.PositionSizing) -> dict:
    return {
        "capital_available": clean_value(s.capital_available),
        "max_loss_per_contract": clean_value(s.max_loss_per_contract),
        "contracts": s.contracts,
        "capital_used": clean_value(s.capital_used),
        "capital_used_pct": clean_value(s.capital_used_pct),
        "total_max_profit": clean_value(s.total_max_profit),
        "total_max_loss": clean_value(s.total_max_loss),
    }


@router.get("/strategy/{symbol}/recommend")
def recommend_strategy(
    symbol: str,
    direction: Literal["bullish", "bearish"] = Query(...),
    timeline: Literal["short", "medium", "long"] = Query(...),
    risk: Literal["conservative", "moderate", "aggressive"] = Query(...),
    capital: float | None = Query(None, gt=0.0, description="Capital available, used to size the position"),
) -> dict:
    symbol = symbol.upper()
    bundle = _load_bundle(symbol)
    chain = bundle.chain
    if chain is None or chain.empty:
        raise HTTPException(status_code=404, detail=f"No option chain data for '{symbol}'.")

    rec = strategy_engine.recommend_trade(chain, direction=direction, timeline=timeline, risk=risk, capital=capital)

    commentary = None
    if rec is not None:
        score_row = None
        try:
            expiry_scores = decision_engine.score_expiries(bundle.metrics, _iv_zscore_lookup(symbol, bundle.metrics))
        except ValueError:
            expiry_scores = None
        if expiry_scores is not None and not expiry_scores.empty:
            match = expiry_scores[expiry_scores["expiration"].dt.date == pd.Timestamp(rec.candidate.expiration).date()]
            if not match.empty:
                score_row = match.iloc[0]
        commentary = insights.recommendation_commentary(score_row, rec.candidate)

    return {
        "symbol": symbol,
        "direction": direction,
        "timeline": timeline,
        "risk": risk,
        "spot": _underlying_price(chain),
        "recommendation": _candidate_record(rec.candidate) if rec else None,
        "sizing": _sizing_record(rec.sizing) if rec and rec.sizing else None,
        "commentary": commentary,
    }


# ---------------------------------------------------------------------------
# Trade Ideas — cross-symbol feed of real, actionable example trades
# (src/dashboard/insights.py), moontower-style. Unlike /api/scanner (always
# 20 rows, nulls for thin data), a symbol only appears here if it actually
# has a directional edge -- expiry_commentary() returns example_trade=None
# for a "Balanced" skew_bias, which correctly means no idea, not missing data.
# ---------------------------------------------------------------------------


_TRADE_IDEA_MAX_DTE = 60  # near-dated only -- see plan: far-dated expiries are illiquid/wide-spread
                          # and not what a weekly-income-style feed should surface.
_HIGH_CONVICTION_Z = 1.5  # meaningfully rich, not just barely (the plain "Rich" threshold is 0.5) --
                          # only reach for an undefined-risk CSP/Covered Call at this conviction level.
_DEFAULT_MAX_TRADE_IDEAS = 8  # "efficient, not overloaded" (explicit user requirement) -- every
                          # qualifying symbol could mean up to 20 cards, so keep only the most
                          # notable signals rather than everything that happens to clear the bar.


def _trade_idea(symbol: str) -> dict | None:
    """One trade idea for `symbol`, built from the exact same "notable
    expiry" selection and insights.expiry_commentary() call overview()'s
    own commentary block already uses -- so an idea here is always
    consistent with what that symbol's Expiry Drilldown would independently
    show, never a second parallel calculation. Returns None (never raises)
    when there's nothing to show: no snapshot yet, no scoreable expiries
    within the DTE window, or the notable expiry's skew_bias is "Balanced"
    at moderate conviction (expiry_commentary() itself returns
    example_trade=None there -- expected, not an error).

    At high conviction (richness_z >= _HIGH_CONVICTION_Z) on a "Rich"
    expiry, prefers an undefined-risk Cash Secured Put or Covered Call over
    the usual defined-risk vertical -- see strategy_engine.build_cash_secured_put()/
    build_covered_call() docstrings for the payoff math, and the plan for
    why this is gated on richness_z (the practically-available signal) even
    though it doesn't strictly require richness_basis=="vrp" (real VRP data
    is rarely available now that price history isn't collected)."""
    info = SYMBOL_REGISTRY[symbol]
    try:
        bundle = data_loader.load_latest_snapshot(symbol)
    except FileNotFoundError:
        return None

    metrics = _filter_metrics_by_dte(bundle.metrics, 0, _TRADE_IDEA_MAX_DTE)
    try:
        expiry_scores = decision_engine.score_expiries(metrics, _iv_zscore_lookup(symbol, metrics))
    except ValueError:
        return None
    if expiry_scores is None or expiry_scores.empty:
        return None

    richness_valid = expiry_scores.dropna(subset=["richness_z"])
    if not richness_valid.empty:
        notable_row = expiry_scores.loc[richness_valid["richness_z"].abs().idxmax()]
    else:
        skew_valid = expiry_scores[expiry_scores["has_wing_data"]].dropna(subset=["skew"])
        if skew_valid.empty:
            return None
        notable_row = expiry_scores.loc[skew_valid["skew"].abs().idxmax()]

    commentary = insights.expiry_commentary(bundle.chain, notable_row)

    richness_z = notable_row.get("richness_z")
    richness_z = float(richness_z) if richness_z is not None and pd.notna(richness_z) else None
    high_conviction = richness_z is not None and richness_z >= _HIGH_CONVICTION_Z
    skew_bias = str(notable_row["skew_bias"])
    expiration = notable_row["expiration"]

    trade = None
    reason = None
    if high_conviction and notable_row["richness_label"] == "Rich":
        if skew_bias in ("Puts richer", "Balanced"):
            trade = strategy_engine.build_cash_secured_put(bundle.chain, expiration)
        elif skew_bias == "Calls richer":
            trade = strategy_engine.build_covered_call(bundle.chain, expiration, _underlying_price(bundle.chain))
        if trade is not None:
            reason = insights.high_conviction_trade_reason(trade, richness_z, notable_row["richness_basis"], skew_bias)
    if trade is None:
        trade = commentary.example_trade
        reason = commentary.trade_angle
    if trade is None:
        return None  # Balanced skew_bias at moderate conviction -- no directional edge, correctly no idea here.

    reward_risk = clean_value(trade.max_profit / trade.max_loss) if trade.max_loss > 0 else None

    return {
        # structure/direction/expiration/dte/legs/net_debit_credit/max_profit/
        # max_loss/breakevens/approx_pop/payoff all come from here -- same
        # serialization Strategy Builder and Backtest already use, so this
        # picks up payoff (needed for Trade Ideas' own payoff chart) for free
        # instead of a second hand-rolled, payoff-less copy of the same dict.
        **_candidate_record(trade),
        "symbol": symbol,
        "color": info.color,
        "underlying_price": _underlying_price(bundle.chain),
        "as_of": bundle.as_of.isoformat(),
        "headline": commentary.headline,
        "reason": reason,
        "is_credit": trade.net_debit_credit < 0,
        "reward_risk": reward_risk,
        "richness_label": clean_value(notable_row["richness_label"]),
        "richness_z": clean_value(notable_row["richness_z"]),
        "richness_basis": clean_value(notable_row["richness_basis"]),
        "skew_bias": clean_value(notable_row["skew_bias"]),
        "skew": clean_value(notable_row["skew"]),
        "has_wing_data": bool(notable_row["has_wing_data"]),
    }


@router.get("/trade-ideas")
def trade_ideas(limit: int = Query(_DEFAULT_MAX_TRADE_IDEAS, ge=1, le=20)) -> dict:
    ideas = [idea for sym in SYMBOL_REGISTRY if (idea := _trade_idea(sym)) is not None]
    # Ranked by |richness_z| -- the same "how notable is this" signal that
    # already picks which expiry counts as notable within one symbol, now
    # also picking which symbols are worth a card at all. A richness_z of
    # None (shouldn't happen once a trade exists, but stay defensive) sorts
    # last rather than crashing the sort.
    ideas.sort(key=lambda i: abs(i["richness_z"]) if i["richness_z"] is not None else -1.0, reverse=True)
    return {"ideas": ideas[:limit]}


# ---------------------------------------------------------------------------
# History (IV Rank / trailing z-score) — bonus stat-tile data
# ---------------------------------------------------------------------------


@router.get("/history/{symbol}/iv-rank")
def iv_rank(
    symbol: str,
    target_dte: int = Query(30, ge=0, le=3650),
    lookback_days: int = Query(365, ge=1, le=36500),
) -> dict | None:
    symbol = symbol.upper()
    if symbol not in SYMBOL_REGISTRY:
        raise HTTPException(status_code=404, detail=f"Unknown symbol '{symbol}'.")
    return _history_store.iv_rank(symbol, target_dte=target_dte, lookback_days=lookback_days)


@router.get("/history/{symbol}/zscore")
def trailing_zscore(
    symbol: str,
    metric: Literal["atm_iv", "skew", "curvature", "realized_vol", "vrp"] = Query("skew"),
    target_dte: int = Query(30, ge=0, le=3650),
    lookback_days: int = Query(90, ge=1, le=36500),
) -> dict | None:
    symbol = symbol.upper()
    if symbol not in SYMBOL_REGISTRY:
        raise HTTPException(status_code=404, detail=f"Unknown symbol '{symbol}'.")
    return _history_store.trailing_zscore(symbol, metric, target_dte=target_dte, lookback_days=lookback_days)


@router.get("/history/{symbol}/metric-series")
def metric_series(
    symbol: str,
    metric: Literal["atm_iv", "skew", "curvature", "realized_vol", "vrp"] = Query("atm_iv"),
    target_dte: int = Query(30, ge=0, le=3650),
    lookback_days: int = Query(365, ge=1, le=36500),
) -> dict:
    """Full day-over-day time series (not just the single current-day iv-rank/
    zscore stat) for plotting a historical trend chart."""
    symbol = symbol.upper()
    if symbol not in SYMBOL_REGISTRY:
        raise HTTPException(status_code=404, detail=f"Unknown symbol '{symbol}'.")
    df = _history_store.metric_series(symbol, metric, target_dte=target_dte, lookback_days=lookback_days)
    return {"symbol": symbol, "metric": metric, "series": df_records(df)}


# ---------------------------------------------------------------------------
# Live refresh — mirrors src/dashboard/app.py's "Refresh Live Data" sidebar
# button. See module docstring: mutating + credential-backed, personal-use
# only for now.
# ---------------------------------------------------------------------------


@router.post("/refresh")
def refresh(
    symbols: str = Query(..., description="Comma-separated symbols to refresh, e.g. 'SPX,AAPL'"),
) -> dict:
    requested = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    if not requested:
        raise HTTPException(status_code=400, detail="No symbols provided.")

    unknown = [sym for sym in requested if sym not in SYMBOL_REGISTRY]
    if unknown:
        raise HTTPException(status_code=404, detail=f"Unknown symbol(s): {', '.join(unknown)}")

    succeeded: list[str] = []
    unavailable: list[dict] = []
    failed: list[dict] = []
    for sym in requested:
        info = SYMBOL_REGISTRY[sym]
        try:
            data_loader.trigger_live_refresh(
                api_symbol=info.api_symbol,
                save_symbol=sym,
                strike_increment=info.strike_increment,
                strikes_each_side=info.strikes_each_side,
            )
            succeeded.append(sym)
        except LiveDataUnavailableError as exc:
            # Not a failure: job.py already refused to overwrite the last good
            # snapshot with this bad pull, so the dashboard still has good data
            # to show (data_loader.load_latest_snapshot falls back automatically).
            unavailable.append({"symbol": sym, "message": str(exc)})
        except FileNotFoundError as exc:
            failed.append({
                "symbol": sym,
                "error": f"{exc} If this is the first run, authenticate first: python -m src.job --first-time.",
            })
        except Exception as exc:  # noqa: BLE001 - surface any live-pull failure per symbol, not a 500 for the whole batch
            failed.append({"symbol": sym, "error": str(exc)})

    return {"succeeded": succeeded, "unavailable": unavailable, "failed": failed}


# ---------------------------------------------------------------------------
# Historical raw data — options + stock_prices tables in the Schwab Database
# (src/schwab_database.py), accumulated by job.py on every successful fetch.
# ---------------------------------------------------------------------------

_schwab_db = SchwabDatabase()


@router.get("/history/{symbol}/price-series")
def price_series(
    symbol: str,
    start: str | None = Query(None, description="ISO date, inclusive"),
    end: str | None = Query(None, description="ISO date, inclusive"),
) -> dict:
    symbol = symbol.upper()
    if symbol not in SYMBOL_REGISTRY:
        raise HTTPException(status_code=404, detail=f"Unknown symbol '{symbol}'.")
    start_date = _parse_date(start, label="start date").date() if start else None
    end_date = _parse_date(end, label="end date").date() if end else None
    df = _schwab_db.price_series(symbol, start=start_date, end=end_date)
    return {"symbol": symbol, "prices": df_records(df)}


@router.get("/history/{symbol}/options-snapshot-dates")
def options_snapshot_dates(symbol: str) -> dict:
    symbol = symbol.upper()
    if symbol not in SYMBOL_REGISTRY:
        raise HTTPException(status_code=404, detail=f"Unknown symbol '{symbol}'.")
    dates = _schwab_db.options_snapshot_dates(symbol)
    return {"symbol": symbol, "dates": [d.isoformat() for d in dates]}


@router.get("/history/{symbol}/options-snapshot")
def options_snapshot(symbol: str, snapshot_date: str = Query(..., description="ISO date")) -> dict:
    symbol = symbol.upper()
    if symbol not in SYMBOL_REGISTRY:
        raise HTTPException(status_code=404, detail=f"Unknown symbol '{symbol}'.")
    parsed = _parse_date(snapshot_date, label="snapshot date").date()
    df = _schwab_db.options_snapshot(symbol, parsed)
    return {"symbol": symbol, "snapshot_date": snapshot_date, "contracts": df_records(df)}


@router.get("/history/{symbol}/contract/{contract_symbol}")
def contract_history(symbol: str, contract_symbol: str) -> dict:
    symbol = symbol.upper()
    if symbol not in SYMBOL_REGISTRY:
        raise HTTPException(status_code=404, detail=f"Unknown symbol '{symbol}'.")
    df = _schwab_db.contract_history(symbol, contract_symbol)
    return {"symbol": symbol, "contract_symbol": contract_symbol, "history": df_records(df)}


# ---------------------------------------------------------------------------
# Backtest — historical trade simulator (src/dashboard/backtest_engine.py).
# Entry-date options reuse /history/{symbol}/options-snapshot-dates above --
# no separate "dates" endpoint needed, same accumulated SchwabDatabase data.
# ---------------------------------------------------------------------------


def _equity_point_record(p: backtest_engine.EquityPoint) -> dict:
    return {
        "date": p.date.isoformat(),
        "dte_remaining": p.dte_remaining,
        "pnl_per_share": clean_value(p.pnl_per_share),
        "underlying_price": clean_value(p.underlying_price),
    }


def _backtest_result_record(r: backtest_engine.BacktestResult) -> dict:
    return {
        "entry_date": r.entry_date.isoformat(),
        "entry_candidate": _candidate_record(r.entry_candidate),
        "equity_curve": [_equity_point_record(p) for p in r.equity_curve],
        "status": r.status,
        "final_pnl_per_share": clean_value(r.final_pnl_per_share),
        "days_held": r.days_held,
        "summary": r.summary,
    }


def _history_snapshots(symbol: str, since: date) -> dict[date, pd.DataFrame]:
    """Every recorded raw option-chain snapshot for `symbol` strictly after
    `since`, adapted to the camelCase schema build_vertical() expects --
    shared by the /expirations and /run backtest endpoints below."""
    snapshots: dict[date, pd.DataFrame] = {}
    for d in _schwab_db.options_snapshot_dates(symbol):
        if d > since:
            snapshots[d] = backtest_engine.adapt_historical_chain(_schwab_db.options_snapshot(symbol, d))
    return snapshots


@router.get("/backtest/{symbol}/expirations")
def backtest_expirations(symbol: str, entry_date: str = Query(..., description="ISO date")) -> dict:
    symbol = symbol.upper()
    if symbol not in SYMBOL_REGISTRY:
        raise HTTPException(status_code=404, detail=f"Unknown symbol '{symbol}'.")
    parsed = _parse_date(entry_date, label="entry date").date()
    raw = _schwab_db.options_snapshot(symbol, parsed)
    if raw.empty:
        raise HTTPException(status_code=404, detail=f"No recorded option chain for '{symbol}' on {entry_date}.")
    chain = backtest_engine.adapt_historical_chain(raw)
    return {"symbol": symbol, "entry_date": entry_date, "expirations": _expirations_list(chain)}


@router.get("/backtest/{symbol}/run")
def backtest_run(
    symbol: str,
    entry_date: str = Query(..., description="ISO date, must be a recorded snapshot date"),
    expiration: str = Query(..., description="ISO date"),
    structure: backtest_engine.StructureChoice = Query(...),
    target_delta: float = Query(0.30, gt=0.0, lt=0.5),
    width_strikes: int = Query(2, ge=1, le=5),
    back_expiration: str | None = Query(None, description="ISO date, required for calendar_call"),
) -> dict:
    symbol = symbol.upper()
    if symbol not in SYMBOL_REGISTRY:
        raise HTTPException(status_code=404, detail=f"Unknown symbol '{symbol}'.")
    if structure == "calendar_call" and not back_expiration:
        raise HTTPException(status_code=400, detail="back_expiration is required for calendar_call.")
    entry_parsed = _parse_date(entry_date, label="entry date").date()
    expiration_parsed = _parse_date(expiration, label="expiration date")
    back_expiration_parsed = _parse_date(back_expiration, label="back expiration date") if back_expiration else None

    raw_entry = _schwab_db.options_snapshot(symbol, entry_parsed)
    if raw_entry.empty:
        raise HTTPException(status_code=404, detail=f"No recorded option chain for '{symbol}' on {entry_date}.")
    entry_chain = backtest_engine.adapt_historical_chain(raw_entry)

    snapshots = _history_snapshots(symbol, entry_parsed)
    result = backtest_engine.run_backtest(
        entry_parsed,
        entry_chain,
        snapshots,
        expiration_parsed,
        structure,
        target_delta,
        width_strikes,
        back_expiration_parsed,
    )

    if result is None:
        return {
            "symbol": symbol,
            "entry_date": entry_date,
            "expiration": expiration,
            "structure": structure,
            "target_delta": target_delta,
            "width_strikes": width_strikes,
            "back_expiration": back_expiration,
            "result": None,
            "error": (
                f"Couldn't build a {structure.replace('_', ' ')} for {symbol} on {entry_date} at this expiration -- "
                "try a different date, expiration, delta, or width."
            ),
        }

    return {
        "symbol": symbol,
        "entry_date": entry_date,
        "expiration": expiration,
        "structure": structure,
        "target_delta": target_delta,
        "width_strikes": width_strikes,
        "back_expiration": back_expiration,
        "result": _backtest_result_record(result),
        "error": None,
    }
