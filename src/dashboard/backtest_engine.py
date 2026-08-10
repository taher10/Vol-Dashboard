"""
src/dashboard/backtest_engine.py

Historical trade simulator: open a real vertical spread at a real recorded
past date's actual prices (via strategy_engine.build_vertical()), then walk
it forward day-by-day using subsequent real recorded snapshots to mark it to
market. Not a multi-year statistical backtest -- the pipeline has only been
accumulating raw daily option-chain snapshots (SchwabDatabase) for a short
while, so this answers "how would this real trade actually have played out
since the day I opened it" rather than "what's the expected return over
thousands of trades." Grows more useful every day the daily-snapshot job
keeps running.

Pure pandas/no I/O, same house style as decision_engine.py/strategy_engine.py
/insights.py -- callers (src/api/routes.py) do the SchwabDatabase reads and
hand snapshots in here already adapted via adapt_historical_chain().
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Literal

import pandas as pd

from src.dashboard import strategy_engine
from src.dashboard.strategy_engine import Candidate, Direction, Leg, RiskAppetite

# SchwabDatabase.options_snapshot() column -> the camelCase build_vertical()
# (and this module's own settlement/MTM helpers) expect. dte/delta/bid/ask
# already match; not listed here.
_COLUMN_RENAME = {
    "option_type": "optionType",
    "strike_price": "strikePrice",
    "underlying_price": "underlyingPrice",
}


def adapt_historical_chain(raw: pd.DataFrame) -> pd.DataFrame:
    """Renames one day's raw SchwabDatabase.options_snapshot() output into the
    camelCase schema strategy_engine.build_vertical() already consumes for
    live chains, so a historical snapshot is just another chain DataFrame to
    it -- no changes needed to strategy_engine.py itself."""
    if raw is None or raw.empty:
        return raw
    df = raw.rename(columns=_COLUMN_RENAME)
    df["expiration"] = pd.to_datetime(df["expiration"])
    return df


def _underlying_price(chain: pd.DataFrame | None) -> float | None:
    """Same lookup as src/api/routes.py's _underlying_price() -- duplicated
    (not imported) deliberately: this module stays a pure dashboard-layer
    module with no dependency on the api layer, matching decision_engine.py/
    strategy_engine.py's own layering."""
    if chain is None or chain.empty or "underlyingPrice" not in chain.columns:
        return None
    values = chain["underlyingPrice"].dropna()
    return float(values.iloc[0]) if not values.empty else None


def intrinsic_value(leg: Leg, underlying_price: float) -> float:
    """Settlement-day value of one leg at expiration -- the same intrinsic-
    value formula _summarize() uses internally (vectorized there, across a
    sampled price range for charting; scalar here, for one actual settlement
    price). Kept as a small standalone duplicate rather than refactoring
    _summarize()'s tested numpy core to share it."""
    if leg.optionType == "CALL":
        return max(underlying_price - leg.strike, 0.0)
    return max(leg.strike - underlying_price, 0.0)


def _leg_mid_on_day(day_chain: pd.DataFrame, leg: Leg, expiration: pd.Timestamp) -> float | None:
    """This leg's mid price on a later day, matched by (expiration, optionType,
    strike) within day_chain -- Leg doesn't carry a contract_symbol, and
    `expiration` must be part of the match key: many different expirations
    list the same strike, so (optionType, strike) alone can silently grab a
    same-strike contract from a completely different expiration and produce
    a nonsense price (confirmed by hand against real SPX data while building
    this)."""
    match = day_chain[
        (day_chain["expiration"] == expiration)
        & (day_chain["optionType"] == leg.optionType)
        & (day_chain["strikePrice"] == leg.strike)
    ]
    match = match.dropna(subset=["bid", "ask"])
    if match.empty:
        return None
    row = match.iloc[0]
    return float((row["bid"] + row["ask"]) / 2.0)


def mark_to_market(legs: list[Leg], day_chain: pd.DataFrame, expiration: pd.Timestamp) -> float | None:
    """Unrealized per-share P&L on `day_chain` vs. each leg's entry mid.
    Returns None (a gap day, skipped by the caller) if any leg's contract
    isn't found that day rather than silently mispricing the position."""
    total = 0.0
    for leg in legs:
        mid = _leg_mid_on_day(day_chain, leg, expiration)
        if mid is None:
            return None
        total += (mid - leg.mid) if leg.action == "buy" else (leg.mid - mid)
    return total


@dataclass
class EquityPoint:
    date: date
    dte_remaining: int
    pnl_per_share: float
    underlying_price: float | None


@dataclass
class BacktestResult:
    entry_date: date
    entry_candidate: Candidate
    equity_curve: list[EquityPoint] = field(default_factory=list)
    status: Literal["open", "closed"] = "open"
    final_pnl_per_share: float = 0.0
    days_held: int = 0
    summary: str = ""


def _summary_sentence(
    entry_date: date, candidate: Candidate, latest: EquityPoint, status: Literal["open", "closed"], days_held: int
) -> str:
    entry_label = entry_date.strftime("%b %d")

    if days_held == 0:
        return (
            f"Opened {entry_label} -- this {candidate.structure} was just entered, no walk-forward history "
            f"recorded yet. Check back as more daily snapshots accumulate."
        )

    pnl = latest.pnl_per_share
    pct_of_max: float | None = None
    if pnl >= 0 and candidate.max_profit > 0:
        pct_of_max = pnl / candidate.max_profit * 100
    elif pnl < 0 and candidate.max_loss > 0:
        pct_of_max = abs(pnl) / candidate.max_loss * 100
    pct_clause = f" ({pct_of_max:.0f}% of max {'profit' if pnl >= 0 else 'loss'})" if pct_of_max is not None else ""
    day_word = "day" if days_held == 1 else "days"

    if status == "closed":
        outcome = "a profit" if pnl > 0 else "a loss" if pnl < 0 else "breakeven"
        return (
            f"Opened {entry_label} -- this {candidate.structure} has since expired, settling at {outcome} of "
            f"${abs(pnl):.2f}/share{pct_clause} after {days_held} real trading {day_word} held."
        )

    direction_word = "up" if pnl >= 0 else "down"
    return (
        f"Opened {entry_label} -- {days_held} real trading {day_word} later, this {candidate.structure} is "
        f"{direction_word} ${abs(pnl):.2f}/share{pct_clause}, still open with {latest.dte_remaining} DTE remaining."
    )


def run_backtest(
    entry_date: date,
    entry_chain: pd.DataFrame,
    snapshots: dict[date, pd.DataFrame],
    expiration: pd.Timestamp,
    direction: Direction,
    risk: RiskAppetite,
) -> BacktestResult | None:
    """
    Opens a vertical spread on `entry_chain` (risk appetite fixes structure/
    delta/width via strategy_engine.RISK_PROFILES, same as Strategy Builder),
    then walks `snapshots` (every later recorded day for this symbol, already
    adapt_historical_chain()-adapted, keyed by date -- strictly after
    entry_date) in order, marking the position to market each day. Once a
    day reaches or passes `expiration`, settles via intrinsic value against
    that day's recorded underlying price and stops. Returns None if no
    vertical could be built for this (expiration, direction, risk) on
    entry_chain -- same "no trade found" semantics as recommend_trade().
    """
    profile = strategy_engine.RISK_PROFILES[risk]
    entry_candidate = strategy_engine.build_vertical(
        entry_chain, expiration, direction, profile.structure, profile.target_short_delta, profile.width_strikes
    )
    if entry_candidate is None:
        return None

    legs = entry_candidate.legs
    expiration_date = pd.Timestamp(expiration).date()

    equity_curve: list[EquityPoint] = [
        EquityPoint(
            date=entry_date,
            dte_remaining=entry_candidate.dte,
            pnl_per_share=0.0,
            underlying_price=_underlying_price(entry_chain),
        )
    ]
    status: Literal["open", "closed"] = "open"

    for day in sorted(snapshots):
        day_chain = snapshots[day]
        dte_remaining = (expiration_date - day).days

        if day >= expiration_date:
            underlying = _underlying_price(day_chain)
            if underlying is None:
                break  # can't settle without a spot price -- stay "open" as of the last good day
            pnl = sum(
                (intrinsic_value(leg, underlying) - leg.mid)
                if leg.action == "buy"
                else (leg.mid - intrinsic_value(leg, underlying))
                for leg in legs
            )
            equity_curve.append(
                EquityPoint(date=day, dte_remaining=max(dte_remaining, 0), pnl_per_share=pnl, underlying_price=underlying)
            )
            status = "closed"
            break

        pnl = mark_to_market(legs, day_chain, expiration)
        if pnl is None:
            continue  # gap day (this contract wasn't captured that day) -- skip, don't break the walk
        equity_curve.append(
            EquityPoint(date=day, dte_remaining=dte_remaining, pnl_per_share=pnl, underlying_price=_underlying_price(day_chain))
        )

    days_held = len(equity_curve) - 1
    final_pnl = equity_curve[-1].pnl_per_share
    summary = _summary_sentence(entry_date, entry_candidate, equity_curve[-1], status, days_held)

    return BacktestResult(
        entry_date=entry_date,
        entry_candidate=entry_candidate,
        equity_curve=equity_curve,
        status=status,
        final_pnl_per_share=final_pnl,
        days_held=days_held,
        summary=summary,
    )
