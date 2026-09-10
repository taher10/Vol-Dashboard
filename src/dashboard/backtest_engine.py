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
from src.dashboard.strategy_engine import Candidate, Leg

# The 7 structures backtestable end to end -- every structure the rest of
# the app already knows how to build (strategy_engine.py), exposed directly
# instead of only reachable indirectly through RISK_PROFILES' 3 fixed
# presets. Verticals map to (direction, debit/credit) for build_vertical();
# the two single-leg structures go straight to their own builders.
# calendar_call is the odd one out -- its two legs expire on different
# dates, which this whole module otherwise assumes never happens; see
# mark_to_market()'s per-leg expiration fallback and run_backtest()'s
# calendar-specific walk branch below.
StructureChoice = Literal[
    "bull_call", "bull_put", "bear_call", "bear_put", "cash_secured_put", "covered_call", "calendar_call"
]

_VERTICAL_PARAMS: dict[StructureChoice, tuple[str, str]] = {
    "bull_call": ("bullish", "debit"),
    "bull_put": ("bullish", "credit"),
    "bear_call": ("bearish", "credit"),
    "bear_put": ("bearish", "debit"),
}

# SchwabDatabase.options_snapshot() column -> the camelCase build_vertical()
# (and this module's own settlement/MTM helpers) expect. dte/delta/bid/ask/
# vega already match; not listed here.
_COLUMN_RENAME = {
    "option_type": "optionType",
    "strike_price": "strikePrice",
    "underlying_price": "underlyingPrice",
    "implied_volatility": "impliedVolatility",  # needed by build_calendar_call()
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


def mark_to_market(
    legs: list[Leg],
    day_chain: pd.DataFrame,
    expiration: pd.Timestamp,
    stock_entry_price: float | None = None,
) -> float | None:
    """Unrealized per-share P&L on `day_chain` vs. each leg's entry mid, plus
    (day underlying - stock_entry_price) when `stock_entry_price` is given --
    the Covered Call case, which also owns 100 shares per contract and needs
    their P&L added on top of the option leg's, exactly the way
    strategy_engine.build_covered_call()'s own payoff curve already does
    (its `stock_pnl = prices - underlying_price` line). Returns None (a gap
    day, skipped by the caller) if any leg's contract isn't found that day,
    or -- for the covered-call case -- the day has no recorded underlying
    price, rather than silently mispricing the position."""
    total = 0.0
    for leg in legs:
        # A calendar's legs carry their own expiration (different from each
        # other); every other structure's legs leave this None and fall
        # back to the one shared `expiration` this function was called with.
        mid = _leg_mid_on_day(day_chain, leg, leg.expiration or expiration)
        if mid is None:
            return None
        total += (mid - leg.mid) if leg.action == "buy" else (leg.mid - mid)
    if stock_entry_price is not None:
        day_underlying = _underlying_price(day_chain)
        if day_underlying is None:
            return None
        total += day_underlying - stock_entry_price
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
    pnl_scaled = pnl * strategy_engine.CONTRACT_MULTIPLIER  # candidate.max_profit/max_loss are already contract-scaled
    pct_of_max: float | None = None
    if pnl >= 0 and candidate.max_profit is not None and candidate.max_profit > 0:
        pct_of_max = pnl_scaled / candidate.max_profit * 100
    elif pnl < 0 and candidate.max_loss is not None and candidate.max_loss > 0:
        pct_of_max = abs(pnl_scaled) / candidate.max_loss * 100
    pct_clause = f" ({pct_of_max:.0f}% of max {'profit' if pnl >= 0 else 'loss'})" if pct_of_max is not None else ""
    day_word = "day" if days_held == 1 else "days"

    # Calendars never intrinsic-settle (see run_backtest()) -- status stays
    # "open" forever, and there's no max_profit/max_loss to frame pnl
    # against. The walk itself is still 100% real recorded prices; only the
    # framing differs.
    if candidate.structure == "Calendar Call Spread":
        direction_word = "up" if pnl >= 0 else "down"
        return (
            f"Opened {entry_label} -- {days_held} real trading {day_word} later, this calendar is "
            f"{direction_word} ${abs(pnl):.2f}/share, marked using real recorded option prices for both legs "
            f"through {latest.date.strftime('%b %d')} (the most recent day both legs were still quoted). "
            f"Not modeled beyond that point."
        )

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
    structure: StructureChoice,
    target_delta: float = 0.30,
    width_strikes: int = 2,
    back_expiration: pd.Timestamp | None = None,
) -> BacktestResult | None:
    """
    Opens `structure` on `entry_chain` at `target_delta` (`width_strikes`
    only used for the 4 vertical structures; `back_expiration` only used for
    calendar_call, ignored otherwise), then walks `snapshots` (every later
    recorded day for this symbol, already adapt_historical_chain()-adapted,
    keyed by date -- strictly after entry_date) in order, marking the
    position to market each day.

    For every structure except calendar_call: once a day reaches or passes
    `expiration`, settles via intrinsic value against that day's recorded
    underlying price and stops. calendar_call never settles this way (its
    long leg still has real time value at the short leg's expiration, which
    intrinsic value would get wrong) -- it just keeps marking both legs to
    market using real recorded prices for as long as both are still quoted,
    and naturally stops producing new points once the front leg's contract
    is no longer in the data (it actually expired), staying "open".

    Returns None if no such structure could be built on entry_chain -- same
    "no trade found" semantics as recommend_trade().
    """
    entry_underlying = _underlying_price(entry_chain)

    if structure in _VERTICAL_PARAMS:
        direction, spread_type = _VERTICAL_PARAMS[structure]
        entry_candidate = strategy_engine.build_vertical(
            entry_chain, expiration, direction, spread_type, target_delta, width_strikes
        )
    elif structure == "cash_secured_put":
        entry_candidate = strategy_engine.build_cash_secured_put(entry_chain, expiration, target_delta)
    elif structure == "covered_call":
        if entry_underlying is None:
            return None
        entry_candidate = strategy_engine.build_covered_call(entry_chain, expiration, entry_underlying, target_delta)
    elif structure == "calendar_call":
        if back_expiration is None:
            return None
        entry_candidate = strategy_engine.build_calendar_call(entry_chain, expiration, back_expiration, target_delta)
    else:
        raise ValueError(f"Unknown structure {structure!r}")

    if entry_candidate is None:
        return None

    legs = entry_candidate.legs
    expiration_date = pd.Timestamp(expiration).date()
    # Covered Call also owns 100 shares per contract -- their P&L (relative
    # to the price paid on entry) has to be walked forward and settled
    # alongside the option leg's. None for every other structure, which are
    # options-only positions.
    stock_entry_price = entry_underlying if structure == "covered_call" else None

    equity_curve: list[EquityPoint] = [
        EquityPoint(
            date=entry_date,
            dte_remaining=entry_candidate.dte,
            pnl_per_share=0.0,
            underlying_price=entry_underlying,
        )
    ]
    status: Literal["open", "closed"] = "open"

    for day in sorted(snapshots):
        day_chain = snapshots[day]
        dte_remaining = (expiration_date - day).days

        if structure != "calendar_call" and day >= expiration_date:
            underlying = _underlying_price(day_chain)
            if underlying is None:
                break  # can't settle without a spot price -- stay "open" as of the last good day
            pnl = sum(
                (intrinsic_value(leg, underlying) - leg.mid)
                if leg.action == "buy"
                else (leg.mid - intrinsic_value(leg, underlying))
                for leg in legs
            )
            if stock_entry_price is not None:
                pnl += underlying - stock_entry_price
            equity_curve.append(
                EquityPoint(date=day, dte_remaining=max(dte_remaining, 0), pnl_per_share=pnl, underlying_price=underlying)
            )
            status = "closed"
            break

        pnl = mark_to_market(legs, day_chain, expiration, stock_entry_price=stock_entry_price)
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
