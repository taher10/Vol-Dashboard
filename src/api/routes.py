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

from typing import Literal

import pandas as pd
from fastapi import APIRouter, HTTPException, Query

from src.data_quality import LiveDataUnavailableError
from src.dashboard import data_loader, decision_engine, strategy_engine
from src.dashboard.data_loader import SnapshotBundle
from src.history_store import HistoryStore
from src.schwab_database import SchwabDatabase
from src.symbols import SYMBOL_REGISTRY

from .schemas import SymbolOut
from .serialize import clean_value, df_records, series_record

router = APIRouter(prefix="/api")

_METRIC_NAMES = ("term_structure", "skew", "skew_ratio", "curvature", "vrp")


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


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


def _match_expiration(df: pd.DataFrame, expiration: str, col: str = "expiration") -> pd.DataFrame:
    target = _parse_date(expiration, label="expiration date").date()
    return df[df[col].dt.date == target]


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
            "term_structure": df_records(metrics.get("term_structure")),
            "skew": df_records(metrics.get("skew")),
            "curvature": df_records(metrics.get("curvature")),
            "vrp": df_records(metrics.get("vrp")) if "vrp" in metrics else None,
        }

    expiry_scores = None
    if primary in filtered_metrics and "term_structure" in filtered_metrics[primary]:
        try:
            expiry_scores = decision_engine.score_expiries(filtered_metrics[primary])
        except ValueError:
            expiry_scores = None

    basket_ranks: dict[str, float] = {}
    if len(bundles) > 1:
        for sym, metrics in filtered_metrics.items():
            try:
                scores = decision_engine.score_expiries(metrics)
            except ValueError:
                continue
            valid = scores.dropna(subset=["vrp_z"])
            if not valid.empty:
                basket_ranks[sym] = float(valid.loc[valid["vrp_z"].abs().idxmax(), "vrp_z"])

    takeaway = None
    if expiry_scores is not None:
        takeaway = decision_engine.build_takeaway(primary, expiry_scores, basket_ranks)

    return {
        "primary": primary,
        "as_of": bundles[primary].as_of.isoformat(),
        "requested_symbols": requested,
        "missing_symbols": missing,
        "symbols": payload_symbols,
        "expiry_scores": df_records(expiry_scores) if expiry_scores is not None else [],
        "takeaway": takeaway,
    }


# ---------------------------------------------------------------------------
# Expiry drilldown
# ---------------------------------------------------------------------------


@router.get("/expiry/{symbol}")
def expiry_drilldown(symbol: str, expiration: str | None = Query(None)) -> dict:
    symbol = symbol.upper()
    bundle = _load_bundle(symbol)
    chain = bundle.chain
    if chain is None or chain.empty or "expiration" not in chain.columns:
        raise HTTPException(status_code=404, detail=f"No option chain data for '{symbol}'.")

    expirations = _expirations_list(chain)
    if not expirations:
        raise HTTPException(status_code=404, detail=f"No expirations found for '{symbol}'.")

    if expiration is None:
        expiration = expirations[0]["expiration"]

    expiry_chain = _match_expiration(chain, expiration)
    smile_cols = [c for c in ("optionType", "delta", "impliedVolatility", "strikePrice") if c in expiry_chain.columns]
    smile = df_records(expiry_chain[smile_cols].dropna(subset=["delta", "impliedVolatility"]))

    score_row = None
    neighbors: list[dict] = []
    try:
        expiry_scores = decision_engine.score_expiries(bundle.metrics)
    except ValueError:
        expiry_scores = None

    if expiry_scores is not None and not expiry_scores.empty:
        target_date = _parse_date(expiration, label="expiration date").date()
        match_idx = expiry_scores.index[expiry_scores["expiration"].dt.date == target_date].tolist()
        if match_idx:
            idx = match_idx[0]
            score_row = series_record(expiry_scores.iloc[idx])
            if idx > 0:
                neighbors.append({"position": "previous", **series_record(expiry_scores.iloc[idx - 1])})
            neighbors.append({"position": "selected", **series_record(expiry_scores.iloc[idx])})
            if idx < len(expiry_scores) - 1:
                neighbors.append({"position": "next", **series_record(expiry_scores.iloc[idx + 1])})

    return {
        "symbol": symbol,
        "expiration": expiration,
        "expirations": expirations,
        "smile": smile,
        "score": score_row,
        "neighbors": neighbors,
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

    return {
        "symbol": symbol,
        "direction": direction,
        "timeline": timeline,
        "risk": risk,
        "spot": _underlying_price(chain),
        "recommendation": _candidate_record(rec.candidate) if rec else None,
        "sizing": _sizing_record(rec.sizing) if rec and rec.sizing else None,
    }


# ---------------------------------------------------------------------------
# History (IV Rank / trailing z-score) — bonus stat-tile data
# ---------------------------------------------------------------------------

_history_store = HistoryStore()


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
